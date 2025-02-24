import abc
import os
import time
import base64
import uuid
import json5 as json
import requests
from XAgent.data_structure.node import ToolNode
from typing import List
from colorama import Fore, Style
from concurrent.futures import ThreadPoolExecutor


from XAgent.utils import ToolCallStatusCode
from XAgent.ai_functions import function_manager


class FunctionHandler:
    """
    The handler for functions.
    """

    def __init__(self, config, logger=None):
        self.logger = logger
        self.config = config

        self.subtask_submit_function = function_manager.get_function_schema(
            "subtask_submit"
        )

        # TODO: support more complex versions of human help, like collaborative debugging.
        self.ask_human_for_help_function = function_manager.get_function_schema(
            "ask_human_for_help"
        )
        self.human_interruption_function = function_manager.get_function_schema(
            "human_interruption"
        )

        self.avaliable_tools_description_list = []

    def log_task_submit(self, arguments):
        """
        Log the task submission.

        Args:
            arguments: The arguments of the task submission.
        """
        self.logger.typewriter_log(
            f"-=-=-=-=-=-=-= SUBTASK SUBMITTED -=-=-=-=-=-=-=",
            Fore.YELLOW,
            "",
        )
        self.logger.typewriter_log(
            f"submit_type:", Fore.YELLOW, f"{arguments['submit_type']}"
        )
        self.logger.typewriter_log(
            f"success:", Fore.YELLOW, f"{arguments['result']['success']}"
        )
        self.logger.typewriter_log(
            f"conclusion:", Fore.YELLOW, f"{arguments['result']['conclusion']}"
        )
        if "milestones" in arguments["result"].keys():
            self.logger.typewriter_log(f"milestones:", Fore.YELLOW)
            for milestone in arguments["result"]["milestones"]:
                line = milestone.lstrip("- ")
                self.logger.typewriter_log("- ", Fore.GREEN, line.strip())
        self.logger.typewriter_log(
            f"need_for_plan_refine:",
            Fore.YELLOW,
            f"{arguments['suggestions_for_latter_subtasks_plan']['need_for_plan_refine']}",
        )
        self.logger.typewriter_log(
            f"plan_suggestions:",
            Fore.YELLOW,
            f"{arguments['suggestions_for_latter_subtasks_plan']['reason']}",
        )

    def change_subtask_handle_function_enum(self, function_name_list: List[str]):
        """
        Change the subtask handling function enumeration.

        Args:
            function_name_list: The list of function names.
        """
        match self.config.default_request_type:
            case "openai":
                self.subtask_handle_function = function_manager.get_function_schema(
                    "subtask_handle"
                )
                self.subtask_handle_function["parameters"]["properties"]["tool_call"][
                    "properties"
                ]["tool_name"]["enum"] = function_name_list
            case "xagent":
                pass
            case _:
                raise NotImplementedError(
                    f"Request type {self.config.default_request_type} not implemented"
                )

    def intrinsic_tools(self, enable_ask_human_for_help):
        """
        Get the intrinsic tools.

        Args:
            enable_ask_human_for_help: Whether to enable the ask_human_for_help function.

        Returns:
            The intrinsic tools.
        """
        tools = [
            self.subtask_submit_function,
        ]
        if enable_ask_human_for_help:
            tools.append(self.ask_human_for_help_function)
        tools.extend(self.avaliable_tools_description_list)
        return tools

    def long_result_summary(self, command: dict, result):
        """
        Summarize the long result.

        Args:
            command (dict): The command.
            result: The result.

        Returns:
            The summarized result.
        """
        if command["name"] == "WebEnv_browse_website":
            if not isinstance(result, str):
                result = str(result)
            result = function_manager(
                "parse_web_text",
                webpage=result[:8096],
                prompt=command["arguments"]["goals_to_browse"],
            )
            result["useful_hyperlinks"] = result["useful_hyperlinks"][:3]
        if command["name"] == "WebEnv_search_and_browse":
            with ThreadPoolExecutor(max_workers=len(result)) as pool:
                f = []
                for ret in result:
                    f.append(
                        pool.submit(
                            function_manager,
                            "parse_web_text",
                            webpage=ret["page"][:8096],
                            prompt=command["arguments"]["goals_to_browse"],
                        )
                    )
                for ret, thd in zip(result, f):
                    ret["page"] = thd.result()
                    ret["page"]["useful_hyperlinks"] = ret["page"]["useful_hyperlinks"][
                        :3
                    ]

        if isinstance(result, str) and len(result) > 2000:
            # need to summarize
            pass
        return result

    def handle_tool_call(self, node: ToolNode):
        """
        Handle the tool call.

        Args:
            node (ToolNode): The tool node.

        Returns:
            The result, tool output status code, whether to refine the plan, and the tools used.
        """
        plan_refine = False
        command_name = node.data["command"]["properties"]["name"]
        arguments = node.data["command"]["properties"]["args"]

        self.logger.typewriter_log(
            "NEXT ACTION: ",
            Fore.CYAN,
            f"COMMAND: {Fore.CYAN}{command_name}{Style.RESET_ALL}  \n"
            f"ARGUMENTS: \n{Fore.CYAN}{arguments}{Style.RESET_ALL}",
        )

        if command_name == "subtask_submit":
            (
                plan_refine,
                tool_output_status_code,
                command_result,
            ) = self.handle_subtask_submit(arguments)
        elif command_name == "ask_human_for_help":
            (
                plan_refine,
                tool_output_status_code,
                command_result,
            ) = self.handle_human_help(arguments)
        elif command_name == "human_interruption":
            assert False, "Never call this function"
        else:
            command_result = ""
            tool_output_status_code = ToolCallStatusCode.TOOL_CALL_SUCCESS

        result = f"Command {command_name} returned: " + f"{command_result}"

        node.data["tool_output"] = command_result
        node.data["tool_status_code"] = tool_output_status_code

        # node.workspace_hash_id = output_hash_id
        if result is not None:
            node.history.add("system", result, "action_result")
            self.logger.typewriter_log("SYSTEM: ", Fore.YELLOW, result)
        else:
            node.history.add("system", "Unable to execute command", "action_result")
            self.logger.typewriter_log(
                "SYSTEM: ", Fore.YELLOW, "Unable to execute command"
            )

        if tool_output_status_code == ToolCallStatusCode.TOOL_CALL_SUCCESS:
            color = Fore.GREEN
        elif tool_output_status_code == ToolCallStatusCode.SUBMIT_AS_SUCCESS:
            color = Fore.YELLOW
        elif tool_output_status_code == ToolCallStatusCode.SUBMIT_AS_FAILED:
            color = Fore.BLUE
        else:
            color = Fore.RED

        self.logger.typewriter_log(
            "TOOL STATUS CODE: ",
            Fore.YELLOW,
            f"{color}{tool_output_status_code.name}{Style.RESET_ALL}",
        )

        using_tools = {
            "tool_name": command_name,
            "tool_input": arguments,
            "tool_output": command_result,
            "tool_status_code": tool_output_status_code.name,
            "thought_data": {
                "thought": node.data["thoughts"],
                "content": node.data["content"],
            },
        }

        if tool_output_status_code in [
            ToolCallStatusCode.SUBMIT_AS_SUCCESS,
            ToolCallStatusCode.SUBMIT_AS_FAILED,
        ]:
            self.log_task_submit(arguments)

        return result, tool_output_status_code, plan_refine, using_tools

    def handle_subtask_submit(self, arguments):
        """
        Handle the subtask submission.

        Args:
            arguments: The arguments of the subtask submission.

        Returns:
            bool: Whether to refine the plan.
            The tool output status code.
            The result.
        """
        plan_refine = False
        if arguments["result"]["success"]:
            tool_output_status_code = ToolCallStatusCode.SUBMIT_AS_SUCCESS
        else:
            tool_output_status_code = ToolCallStatusCode.SUBMIT_AS_FAILED
        if arguments["suggestions_for_latter_subtasks_plan"]["need_for_plan_refine"]:
            plan_refine = True
        answer = {
            "content": f"you have successfully submit the subtask as {arguments['submit_type']}"
        }
        command_result = json.dumps(answer, ensure_ascii=False)

        return plan_refine, tool_output_status_code, command_result

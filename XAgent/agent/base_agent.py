import abc
import json5
from typing import List
from colorama import Fore
from copy import deepcopy

from XAgent.config import CONFIG
from XAgent.utils import LLMStatusCode, RequiredAbilities
from XAgent.message_history import Message
from XAgent.logs import logger
from XAgent.ai_functions import objgenerator


class BaseAgent(metaclass=abc.ABCMeta):
    """
    The BaseAgent class abstracts the essential attributes and methods for classes,
    which inherit it. It is a metaclass of the Abstract Base Class (abc module).

    Attributes:
        abilities (set): A set of RequiredAbilities, which are necessary skills for BaseAgent.
    """

    abilities = set(
        [
            RequiredAbilities.plan_generation,
            RequiredAbilities.plan_refinement,
            RequiredAbilities.task_evaluator,
            RequiredAbilities.tool_tree_search,
            RequiredAbilities.reflection,
            RequiredAbilities.summarization,
            RequiredAbilities.father_reflection,
            RequiredAbilities.simple_search,
            RequiredAbilities.verify_refine,
        ]
    )

    def __init__(self, config, prompt_messages: List[Message] = None):
        """
        Constructs an agent object with set abilities, configuration settings,
        and initial set of prompt messages.

        Args:
            config (obj): Configuration settings for agent.
            prompt_messages (List): Initial set of messages user gives to interact with the agent.
        """
        logger.typewriter_log(
            f"Constructing an Agent:",
            Fore.YELLOW,
            self.__class__.__name__,
        )
        self.config = config
        self.prompt_messages = prompt_messages
        self.usage = {}

    @abc.abstractmethod
    def parse(self, **args) -> (LLMStatusCode, Message, dict):
        """
        Abstract method that needs to be implemented by the subclasses.
        Required for parsing the given arguments.
        """
        pass

    def fill_in_placeholders(self, placeholders: dict):
        """
        Fills in placeholders defined in the input with the corresponding values.

        Args:
            placeholders (dict): A dictionary containing keys as placeholders and values as their replacements.

        Returns:
            filled_messages: A copy of the initial prompt_messages with placeholders replaced with their corresponding values.
        """
        filled_messages = deepcopy(self.prompt_messages)
        for message in filled_messages:
            role = message.role
            if role in placeholders:
                for key, value in placeholders[role].items():
                    message.content = message.content.replace(
                        "{{" + str(key) + "}}", str(value)
                    )
        return filled_messages

    def generate(
        self,
        messages: list[dict] | list[Message],
        arguments: dict = None,
        functions: list[dict] = None,
        function_call: dict = None,
        stop: dict = None,
        *args,
        **kwargs,
    ):
        """
        Generates a response from the AI model, using the given messages, arguments, functions,
        and a function call.

        Args:
            messages (list[dict]|list[Message]): A list of messages with which to interact with the AI model.
            arguments (dict, optional): A dictionary containing arguments to use for AI model responses.
            functions (list[dict], optional): A list of dictionaries representing functions to use for AI model responses.
            function_call (dict, optional): A dictionary representing a function call to use for AI model responses.
            stop (dict, optional): A dictionary that signifies when to stop the conversation with the AI model.
            *args: Variable list of arguments.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            message (dict): A message generated by the AI model.
            tokens (int): Number of tokens used in generating the AI model's response.
        """
        if isinstance(messages[0], Message):
            messages = [message.raw() for message in messages]
        if functions is not None and len(functions) == 1 and function_call is None:
            function_call = {
                "name": functions[0]["name"]
            }  # must call at least one function
        match CONFIG.default_request_type:
            case "openai":
                if arguments is not None:
                    if functions is None or len(functions) == 0:
                        functions = [{"name": "reasoning", "parameters": arguments}]
                        function_call = {"name": "reasoning"}
                    elif len(functions) == 1:
                        for k, v in arguments["properties"].items():
                            functions[0]["parameters"]["properties"][k] = v
                            if k in arguments["required"]:
                                functions[0]["parameters"]["required"].append(k)
                    else:
                        raise NotImplementedError(
                            "Not implemented for multiple functions with arguments"
                        )

                response = objgenerator.chatcompletion(
                    messages=messages,
                    functions=functions,
                    function_call=function_call,
                    stop=stop,
                    *args,
                    **kwargs,
                )

                message = {}
                function_call_args: dict = json5.loads(
                    response["choices"][0]["message"]["function_call"]["arguments"]
                )

                if arguments is not None:
                    message["arguments"] = {
                        k: function_call_args.pop(k)
                        for k in arguments["properties"].keys()
                        if k in function_call_args
                    }
                if len(function_call_args) > 0:
                    message["function_call"] = {
                        "name": response["choices"][0]["message"]["function_call"][
                            "name"
                        ],
                        "arguments": function_call_args,
                    }

            case "xagent":
                response = objgenerator.chatcompletion(
                    messages=messages,
                    arguments=arguments,
                    functions=functions,
                    function_call=function_call,
                    stop=stop,
                    *args,
                    **kwargs,
                )
                message = json5.loads(response["choices"][0]["message"]["content"])
            case _:
                raise NotImplementedError(
                    f"Request type {CONFIG.default_request_type} not implemented"
                )

        tokens = response["usage"]
        return message, tokens

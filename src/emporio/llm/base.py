"""Provider-neutral conversation types and the interface every LLM adapter implements."""

from dataclasses import dataclass, field
from typing import Protocol

from ..tools import Tool


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: str  # JSON envelope
    is_error: bool = False


@dataclass
class UserMessage:
    text: str


@dataclass
class AssistantMessage:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolResultsMessage:
    results: list[ToolResult]


Message = UserMessage | AssistantMessage | ToolResultsMessage


@dataclass
class LLMResponse:
    message: AssistantMessage
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | "refusal" | provider-specific value
    usage: dict = field(default_factory=dict)


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, tools: list[Tool], history: list[Message]) -> LLMResponse:
        """Send the whole conversation and return the model's next assistant turn."""
        ...

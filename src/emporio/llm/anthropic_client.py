"""Claude via the official Anthropic SDK."""

import anthropic

from ..tools import Tool
from .base import AssistantMessage, LLMResponse, Message, ToolCall, ToolResultsMessage, UserMessage

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicClient:
    def __init__(self, model: str, effort: str):
        self.name = f"anthropic:{model}"
        self.model = model
        self.effort = effort
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    def complete(self, system: str, tools: list[Tool], history: list[Message]) -> LLMResponse:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            tools=[{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools],
            messages=to_anthropic_messages(history),
            # Opus 5.5 always thinks; effort is the only depth control (low suits short chat turns).
            output_config={"effort": self.effort},
            # Cache the growing prefix (tools + system + history): the conversation is append-only.
            cache_control={"type": "ephemeral"},
            # If a safety classifier declines, re-run on Anthropic's recommended fallback model.
            fallbacks="default",
            betas=[FALLBACK_BETA],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, b.input) for b in response.content if b.type == "tool_use"]
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_input_tokens": response.usage.cache_read_input_tokens or 0,
        }
        return LLMResponse(AssistantMessage(text, calls, raw=response.content), response.stop_reason, usage)


def to_anthropic_messages(history: list[Message]) -> list[dict]:
    messages = []
    for message in history:
        if isinstance(message, UserMessage):
            messages.append({"role": "user", "content": message.text})
        elif isinstance(message, AssistantMessage):
            content = message.raw if message.raw is not None else [{"type": "text", "text": message.text}]
            messages.append({"role": "assistant", "content": content})
        elif isinstance(message, ToolResultsMessage):
            messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                    for r in message.results
                ],
            })
    return messages

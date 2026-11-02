"""A local model served by Ollama (free, no API key)."""

import json
import uuid

import ollama

from ..tools import Tool
from .base import AssistantMessage, LLMResponse, Message, ToolCall, ToolResultsMessage, UserMessage

CHARS_PER_TOKEN = 3  # rough estimate, conservative for Portuguese text and JSON


class OllamaClient:
    def __init__(self, model: str, host: str, num_ctx: int):
        self.name = f"ollama:{model}"
        self.model = model
        self.num_ctx = num_ctx
        self.client = ollama.Client(host=host)

    def complete(self, system: str, tools: list[Tool], history: list[Message]) -> LLMResponse:
        tool_specs = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
            for t in tools
        ]
        # Ollama silently drops the start of a prompt that exceeds num_ctx, so trim whole old
        # turns ourselves, keeping ~30% of the window free for the answer.
        fixed_chars = len(system) + len(json.dumps(tool_specs))
        history = trim_history(history, int(self.num_ctx * 0.7 * CHARS_PER_TOKEN) - fixed_chars)

        response = self.client.chat(
            model=self.model,
            messages=[{"role": "system", "content": system}, *to_ollama_messages(history)],
            tools=tool_specs,
            think=False,  # Qwen3's thinking mode adds latency without helping tool selection here
            options={"num_ctx": self.num_ctx, "temperature": 0.2},
        )
        message = response.message
        calls = [
            ToolCall(f"call_{uuid.uuid4().hex[:8]}", c.function.name, dict(c.function.arguments))
            for c in message.tool_calls or []
        ]
        stop_reason = "tool_use" if calls else ("max_tokens" if response.done_reason == "length" else "end_turn")
        usage = {"input_tokens": response.prompt_eval_count or 0, "output_tokens": response.eval_count or 0}
        return LLMResponse(AssistantMessage(message.content or "", calls), stop_reason, usage)


def to_ollama_messages(history: list[Message]) -> list[dict]:
    messages = []
    for message in history:
        if isinstance(message, UserMessage):
            messages.append({"role": "user", "content": message.text})
        elif isinstance(message, AssistantMessage):
            entry = {"role": "assistant", "content": message.text}
            if message.tool_calls:
                entry["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in message.tool_calls]
            messages.append(entry)
        elif isinstance(message, ToolResultsMessage):
            messages.extend({"role": "tool", "content": r.content, "tool_name": r.name} for r in message.results)
    return messages


def trim_history(history: list[Message], max_chars: int) -> list[Message]:
    """Drop the oldest whole user turns until the conversation fits; always keep the last turn."""
    def size(messages):
        return len(json.dumps(to_ollama_messages(messages), ensure_ascii=False))

    while size(history) > max_chars:
        starts = [i for i, m in enumerate(history) if isinstance(m, UserMessage)]
        if len(starts) < 2:
            break
        history = history[starts[1]:]
    return history

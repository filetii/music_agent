from emporio.llm.base import AssistantMessage, ToolCall, ToolResult, ToolResultsMessage, UserMessage
from emporio.llm.ollama_client import to_ollama_messages, trim_history

HISTORY = [
    UserMessage("Qual o horário?"),
    AssistantMessage("", [ToolCall("t1", "get_policy", {"sections": ["opening_hours"]})]),
    ToolResultsMessage([ToolResult("t1", "get_policy", '{"status": "ok"}')]),
    AssistantMessage("Abrimos às 9h."),
]


def test_ollama_message_shapes():
    messages = to_ollama_messages(HISTORY)
    assert messages[0] == {"role": "user", "content": "Qual o horário?"}
    assert messages[1]["tool_calls"] == [{"function": {"name": "get_policy", "arguments": {"sections": ["opening_hours"]}}}]
    assert messages[2] == {"role": "tool", "content": '{"status": "ok"}', "tool_name": "get_policy"}
    assert messages[3] == {"role": "assistant", "content": "Abrimos às 9h."}


def test_trim_drops_oldest_whole_turns_and_keeps_last():
    history = HISTORY + [UserMessage("x" * 500)]
    trimmed = trim_history(history, max_chars=600)
    assert trimmed == [history[-1]]
    assert trim_history(HISTORY, max_chars=10_000) == HISTORY

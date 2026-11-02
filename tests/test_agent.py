"""Agent loop tests with a scripted fake LLM (no network)."""

import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from emporio.agent import FAILURE_REPLY, FALLBACK_REPLY, REFUSAL_REPLY, create_session, ungrounded_amounts
from emporio.config import load_settings
from emporio.llm.base import AssistantMessage, LLMResponse, ToolCall, ToolResult, ToolResultsMessage, UserMessage


class ScriptedLLM:
    """Returns pre-programmed responses and records the history it was shown."""

    name = "scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen = []

    def complete(self, system, tools, history):
        self.seen.append((system, list(history)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def tool_call(tool_name, **arguments):
    return LLMResponse(AssistantMessage("", [ToolCall(f"id-{tool_name}", tool_name, arguments)]), "tool_use")


def answer(text):
    return LLMResponse(AssistantMessage(text), "end_turn")


@pytest.fixture
def make_session(tmp_path):
    settings = replace(load_settings(), handoffs_path=tmp_path / "handoffs.jsonl", max_tool_steps=3)

    def make(*responses):
        llm = ScriptedLLM(responses)
        return create_session(settings, llm=llm, now=datetime(2026, 3, 25, 10, 0)), llm

    return make


def test_tool_call_then_answer(make_session):
    session, llm = make_session(tool_call("get_product_details", name="Takamine GD20"), answer("Custa R$ 2.199,00."))
    reply = session.send("Quanto custa o Takamine GD20?")
    assert reply.text == "Custa R$ 2.199,00."
    assert [t.name for t in reply.tool_calls] == ["get_product_details"]
    assert reply.tool_calls[0].result["data"]["product_id"] == 95
    # The second call saw the tool result appended after the assistant's tool call
    _, history = llm.seen[1]
    assert isinstance(history[-1], ToolResultsMessage)
    assert json.loads(history[-1].results[0].content)["status"] == "ok"


def test_invalid_arguments_are_returned_to_the_model(make_session):
    session, llm = make_session(tool_call("get_order_status", order_id=8), answer("Qual o telefone do pedido?"))
    reply = session.send("Cadê meu pedido 8?")
    result = llm.seen[1][1][-1].results[0]
    assert result.is_error and json.loads(result.content)["status"] == "invalid_input"
    assert reply.text == "Qual o telefone do pedido?"


def test_step_limit_stops_the_loop(make_session):
    session, _ = make_session(*[tool_call("get_policy", sections=["opening_hours"])] * 3)
    reply = session.send("Qual o horário?")
    assert reply.text == FALLBACK_REPLY
    assert len(reply.tool_calls) == 3
    assert isinstance(session.history[-1], AssistantMessage)  # conversation stays well-formed


def test_refusal_gets_a_safe_reply(make_session):
    session, _ = make_session(LLMResponse(AssistantMessage(""), "refusal"))
    assert session.send("...").text == REFUSAL_REPLY


def test_failed_turn_is_discarded_and_conversation_continues(make_session):
    session, llm = make_session(answer("Olá!"), RuntimeError("network down"), answer("Abrimos às 9h."))
    session.send("Oi")
    assert session.send("Qual o horário?").text == FAILURE_REPLY
    assert len(session.history) == 2  # the failed turn left nothing behind
    assert session.send("Qual o horário?").text == "Abrimos às 9h."


def test_history_is_append_only_and_system_prompt_frozen(make_session):
    session, llm = make_session(answer("Oi!"), tool_call("get_policy", sections=["warranty"]), answer("90 dias."))
    session.send("Oi")
    session.send("Qual a garantia?")
    first_system, first_history = llm.seen[0]
    for system, history in llm.seen[1:]:
        assert system == first_system
        assert history[: len(first_history)] == first_history
    assert [type(m) for m in session.history] == [
        UserMessage, AssistantMessage, UserMessage, AssistantMessage, ToolResultsMessage, AssistantMessage,
    ]


def test_grounding_check_amounts():
    history = [
        UserMessage("Tem violão até R$1000?"),
        ToolResultsMessage([ToolResult("t", "x", '{"list_price": "R$ 2.199,00"}')]),
    ]
    assert ungrounded_amounts("Custa R$ 2.199,00, ou R$ 1.000,00 no limite.", history) == set()
    assert ungrounded_amounts("Sai por R$ 1.999,00 ou R$ 2.199.", history) == {Decimal("1999.00")}


def test_ungrounded_price_triggers_one_corrective_retry(make_session):
    session, llm = make_session(
        answer("O Takamine GD20 custa R$ 1.999,00!"),  # made up, no tool used
        tool_call("get_product_details", name="Takamine GD20"),
        answer("O Takamine GD20 custa R$ 2.199,00."),
    )
    reply = session.send("Quanto custa o Takamine GD20?")
    assert reply.text == "O Takamine GD20 custa R$ 2.199,00."
    assert reply.guard_retries == 1
    feedback = llm.seen[1][1][-1]
    assert isinstance(feedback, UserMessage) and "R$ 1999.00" in feedback.text


def test_repeated_ungrounded_price_falls_back(make_session):
    session, _ = make_session(answer("Custa R$ 1.999,00."), answer("Custa R$ 1.899,00."))
    assert session.send("Quanto custa o GD20?").text == FALLBACK_REPLY


def test_announcing_a_lookup_without_calling_a_tool_is_corrected(make_session):
    session, llm = make_session(
        answer("Vou verificar a disponibilidade, um momento!"),
        tool_call("get_product_details", name="Takamine GD20"),
        answer("Temos 5 unidades do Takamine GD20."),
    )
    reply = session.send("Tem Takamine GD20?")
    assert reply.text == "Temos 5 unidades do Takamine GD20."
    assert reply.guard_retries == 1
    assert "called no tool" in llm.seen[1][1][-1].text


def test_promotional_price_without_original_price_is_corrected(make_session):
    session, llm = make_session(
        tool_call("get_product_details", product_id=94),
        answer("O Taylor 110e sai por R$ 5.519,08."),
        answer("De R$ 5.999,00 por R$ 5.519,08 (8% de desconto - Desconto Exclusivo)."),
    )
    reply = session.send("Quanto custa o Taylor 110e?")
    assert reply.text.startswith("De R$ 5.999,00 por R$ 5.519,08")
    assert "De R$ 5.999,00 por R$ 5.519,08" in llm.seen[2][1][-1].text  # feedback quotes present_as


def test_presentation_rules_never_withhold_a_correct_answer(make_session):
    session, _ = make_session(
        tool_call("get_product_details", product_id=94),
        answer("Sai por R$ 5.519,08."),
        answer("Sai por R$ 5.519,08 no PIX."),  # still incomplete after the retry
    )
    assert session.send("Quanto custa o Taylor 110e?").text == "Sai por R$ 5.519,08 no PIX."


def test_unavailable_product_without_alternatives_is_corrected(make_session):
    session, llm = make_session(
        tool_call("search_products", query="Giannini GF-3D"),
        answer("O Giannini GF-3D está esgotado no momento."),
        answer("Está esgotado, mas temos o Shelby SGD-195E por R$ 799,00."),
    )
    reply = session.send("Tem o Giannini GF-3D?")
    assert reply.text.endswith("R$ 799,00.")
    assert "§7.3" in llm.seen[2][1][-1].text


def test_order_lookup_needs_identity_typed_by_the_customer(make_session):
    session, llm = make_session(
        tool_call("get_order_status", order_id=123456, phone="(67) 98765-4321"),  # invented by the model
        tool_call("get_order_status", order_id=7, phone="(67) 99789-0123"),
        answer("Encontrei seu pedido."),
    )
    reply = session.send("Meu pedido é o 7, telefone 67 99789-0123. Chegou?")
    assert [t.result["status"] for t in reply.tool_calls] == ["invalid_input", "ok"]


def test_system_prompt_has_session_context_and_empty_categories(make_session):
    session, llm = make_session(answer("Oi!"))
    session.send("Oi")
    system = llm.seen[0][0]
    assert "quarta-feira, 25/03/2026, 10:00" in system
    assert "Instrumentos de Sopro (Madeiras)" in system.split("NO products at all in these categories:")[1]

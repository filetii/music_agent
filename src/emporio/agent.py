"""The agent loop: one customer message in, one reply out, with bounded tool use."""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .config import STORE_TIMEZONE, Settings
from .data import load_store
from .llm import create_client
from .llm.base import AssistantMessage, LLMClient, Message, ToolResult, ToolResultsMessage, UserMessage
from .policies import load_policies
from .prompts import build_system_prompt
from .tools import Tool, ToolContext, ToolRegistry, build_tools

log = logging.getLogger(__name__)

FALLBACK_REPLY = (
    "Desculpe, não consegui concluir sua solicitação agora. "
    "Quer que eu encaminhe seu atendimento para a nossa equipe?"
)
REFUSAL_REPLY = (
    "Desculpe, não posso ajudar com isso. Posso ajudar com algo sobre nossos instrumentos, "
    "seus pedidos ou as políticas da loja?"
)
FAILURE_REPLY = "Estou com uma instabilidade no momento. Pode mandar sua mensagem de novo, por favor?"
CHECK_PREFIX = "[Automatic check - this message comes from the system, not from the customer] "
GROUNDING_FEEDBACK = CHECK_PREFIX + (
    "Your reply mentions amounts that appear in no tool result or customer message of this conversation: "
    "{amounts}. Do not answer from memory: call the appropriate tool and answer again using only the values it returns."
)
STALL_FEEDBACK = CHECK_PREFIX + (
    "You told the customer you would check something, but called no tool. "
    "Call the appropriate tool now and answer with its result."
)
PROMOTION_FEEDBACK = CHECK_PREFIX + (
    "You gave a promotional price without the original price and the discount percentage. The store manual "
    "(§6.2) requires all three: rewrite your answer presenting it as \"{present_as}\"."
)
ALTERNATIVES_FEEDBACK = CHECK_PREFIX + (
    "You told the customer a product is unavailable without suggesting alternatives. The store manual (§7.3) "
    "requires suggesting similar available products: rewrite your answer listing the alternatives from the "
    "tool result, with their prices."
)

_AMOUNT = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{2}))?")
_STALL = re.compile(r"\b(vou|vamos|deixa eu|irei) (verificar|consultar|checar|conferir)\b|\bum (momento|instante)\b", re.I)
_UNAVAILABLE = re.compile(r"esgotad|indispon|descontinuad|em breve|não (está|estão) à venda", re.I)
_PROMOTION = re.compile(r"De (R\$ [\d.,]+) por (R\$ [\d.,]+)")


def _amounts(text: str) -> set[Decimal]:
    return {Decimal(f"{whole.replace('.', '')}.{cents or '00'}") for whole, cents in _AMOUNT.findall(text)}


def ungrounded_amounts(reply: str, history: list[Message]) -> set[Decimal]:
    """R$ amounts in the reply that no tool result or customer message supports."""
    evidence = set()
    for message in history:
        if isinstance(message, UserMessage):
            evidence |= _amounts(message.text)
        elif isinstance(message, ToolResultsMessage):
            for result in message.results:
                evidence |= _amounts(result.content)
    return _amounts(reply) - evidence


def _turn_results(history: list[Message]) -> list[dict]:
    """Tool results since the customer's last message."""
    start = max(
        i for i, m in enumerate(history) if isinstance(m, UserMessage) and not m.text.startswith(CHECK_PREFIX)
    )
    return [json.loads(r.content) for m in history[start:] if isinstance(m, ToolResultsMessage) for r in m.results]


def _dicts(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _dicts(item)


def guard_feedback(reply: str, history: list[Message]) -> tuple[str, bool] | None:
    """Deterministic checks on a final reply.

    Returns (corrective feedback for the model, blocking) or None. Blocking problems (invented
    prices, a promised lookup that never happened) must not reach the customer; the others are
    the manual's presentation rules, worth one retry but not worth withholding a correct answer.
    """
    unsupported = ungrounded_amounts(reply, history)
    if unsupported:
        return GROUNDING_FEEDBACK.format(amounts=", ".join(f"R$ {a}" for a in sorted(unsupported))), True
    if _STALL.search(reply):
        return STALL_FEEDBACK, True

    said = _amounts(reply)
    results = list(_dicts(_turn_results(history)))
    for item in results:
        match = _PROMOTION.match(str(item.get("present_as", "")))
        if match and _amounts(match.group(2)) <= said and not _amounts(match.group(1)) <= said:
            return PROMOTION_FEEDBACK.format(present_as=item["present_as"]), False
    if _UNAVAILABLE.search(reply):
        alternative_prices = {
            a for item in results for alt in item.get("alternatives") or [] for a in _amounts(json.dumps(alt))
        }
        if alternative_prices and not alternative_prices & said:
            return ALTERNATIVES_FEEDBACK, False
    return None


def unsupplied_identity(arguments: dict, history: list[Message]) -> str | None:
    """Order lookups may only use an order number and phone the customer typed in this conversation.

    Stops the model from inventing them (seen in testing) and from looking up orders with data the
    customer never provided (e.g. prompted by an injection)."""
    said = " ".join(
        m.text for m in history if isinstance(m, UserMessage) and not m.text.startswith(CHECK_PREFIX)
    )
    order_ok = str(arguments.get("order_id")) in re.findall(r"\d+", said)
    phone = re.sub(r"\D", "", str(arguments.get("phone", "")))
    phone_ok = len(phone) >= 8 and phone[-8:] in re.sub(r"\D", "", said)
    if order_ok and phone_ok:
        return None
    return (
        "Use only the order number and phone the customer typed in this conversation. "
        "If you do not have them, ask the customer."
    )


@dataclass
class ToolTrace:
    name: str
    arguments: dict
    result: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolTrace] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    guard_retries: int = 0


class Session:
    def __init__(self, llm: LLMClient, registry: ToolRegistry, system_prompt: str, max_steps: int):
        self.llm = llm
        self.registry = registry
        self.tools: list[Tool] = list(registry.tools.values())
        self.system_prompt = system_prompt  # frozen for the whole session
        self.max_steps = max_steps
        self.history: list[Message] = []

    def send(self, text: str) -> Reply:
        turn_start = len(self.history)
        self.history.append(UserMessage(text))
        reply = Reply(text="")
        try:
            for _ in range(self.max_steps):
                response = self.llm.complete(self.system_prompt, self.tools, self.history)
                for key, value in response.usage.items():
                    reply.usage[key] = reply.usage.get(key, 0) + value

                if response.stop_reason == "refusal":
                    return self._finish(reply, REFUSAL_REPLY)
                if response.stop_reason == "max_tokens":
                    raise RuntimeError("model output hit max_tokens")

                if not response.message.tool_calls:
                    # Deterministic guard (see guard_feedback): one corrective retry; if a blocking
                    # problem persists, the customer gets the safe fallback instead.
                    check = guard_feedback(response.message.text, self.history)
                    if check and reply.guard_retries == 0:
                        reply.guard_retries += 1
                        log.warning("guard triggered: %s", check[0])
                        self.history += [response.message, UserMessage(check[0])]
                        continue
                    if check and check[1]:
                        return self._finish(reply, FALLBACK_REPLY)
                    self.history.append(response.message)
                    reply.text = response.message.text.strip()
                    return reply

                self.history.append(response.message)
                results = []
                for call in response.message.tool_calls:
                    problem = (
                        unsupplied_identity(call.arguments, self.history) if call.name == "get_order_status" else None
                    )
                    result = {"status": "invalid_input", "message": problem} if problem else \
                        self.registry.run(call.name, call.arguments)
                    log.info("tool %s %s -> %s", call.name, call.arguments, result["status"])
                    reply.tool_calls.append(ToolTrace(call.name, call.arguments, result))
                    results.append(ToolResult(
                        call.id, call.name, json.dumps(result, ensure_ascii=False),
                        is_error=result["status"] in ("error", "invalid_input"),
                    ))
                self.history.append(ToolResultsMessage(results))
            return self._finish(reply, FALLBACK_REPLY)
        except Exception:
            log.exception("LLM call failed; discarding this turn")
            del self.history[turn_start:]  # drop the unanswered turn so the customer can retry
            reply.text = FAILURE_REPLY
            return reply

    def _finish(self, reply: Reply, text: str) -> Reply:
        self.history.append(AssistantMessage(text))
        reply.text = text
        return reply


def create_session(settings: Settings, llm: LLMClient | None = None, now: datetime | None = None) -> Session:
    """Wire data, tools, prompt and model together. `now` defaults to the reference date at the current local time."""
    store = load_store(settings.data_dir)
    if now is None:
        clock = datetime.now(ZoneInfo(STORE_TIMEZONE))
        now = datetime.combine(settings.reference_date, clock.time().replace(second=0, microsecond=0))
    context = ToolContext(
        store=store,
        policies=load_policies(settings.data_dir / "policies.md"),
        reference_date=settings.reference_date,
        handoffs_path=settings.handoffs_path,
    )
    return Session(
        llm=llm or create_client(settings),
        registry=ToolRegistry(build_tools(store), context),
        system_prompt=build_system_prompt(store, now),
        max_steps=settings.max_tool_steps,
    )

"""Tools the model can call. Each returns an envelope the model can always reason about:

    {"status": "ok" | "not_found" | "ambiguous" | "invalid_input" | "error", "message": ..., "data": ...}

so "the record does not exist" is never confused with "the query failed". Handlers
never raise: validation problems and unexpected exceptions become envelopes too.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from . import pricing
from .data import Product, Store
from .policies import DEADLINES, SECTION_SUMMARIES
from .pricing import brl

log = logging.getLogger(__name__)

ORDER_STATUS_LABELS = {
    "pending": "pendente",
    "confirmed": "confirmado",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}
AVAILABILITY_LABELS = {
    "available": "disponível",
    "out_of_stock": "esgotado - não pode ser comprado agora",
    "discontinued": "descontinuado - não faz mais parte do catálogo",
    "coming_soon": "em breve - ainda não está à venda",
}
ESCALATION_REASONS = ["complaint", "order_issue", "return_or_warranty", "policy_question", "customer_request"]


@dataclass
class ToolContext:
    store: Store
    policies: dict[str, str]
    reference_date: date
    handoffs_path: Path


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[ToolContext, dict], dict]


class InvalidInput(Exception):
    pass


def _envelope(status: str, message: str | None = None, data=None) -> dict:
    result = {"status": status}
    if message:
        result["message"] = message
    if data is not None:
        result["data"] = data
    return result


# --- minimal JSON-schema validation (only the features our schemas use) -------------

_TYPES = {"integer": int, "number": (int, float), "string": str, "boolean": bool, "array": list, "object": dict}


def _validate(value, schema: dict, path: str = "input") -> None:
    expected = _TYPES[schema["type"]]
    if not isinstance(value, expected) or (schema["type"] in ("integer", "number") and isinstance(value, bool)):
        raise InvalidInput(f"{path} must be of type {schema['type']}")
    if "enum" in schema and value not in schema["enum"]:
        raise InvalidInput(f"{path} must be one of {schema['enum']}")
    if "minimum" in schema and value < schema["minimum"]:
        raise InvalidInput(f"{path} must be >= {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        raise InvalidInput(f"{path} must be <= {schema['maximum']}")
    if schema["type"] == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise InvalidInput(f"{path} must have between {schema.get('minItems', 0)} and {schema.get('maxItems')} items")
        for i, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{i}]")
    if schema["type"] == "object":
        unknown = set(value) - set(schema.get("properties", {}))
        if unknown:
            raise InvalidInput(f"{path} has unknown fields {sorted(unknown)}")
        missing = [k for k in schema.get("required", []) if k not in value]
        if missing:
            raise InvalidInput(f"{path} is missing required fields {missing}")
        for key, item in value.items():
            _validate(item, schema["properties"][key], f"{path}.{key}")


# --- serialization helpers -----------------------------------------------------------


def _promotion_text(price: pricing.UnitPrice) -> str:
    """Manual §6.2: promotional prices always shown with the original price and the percentage.
    Ready-made text, because the local model kept dropping the original price."""
    return (
        f"De {brl(price.list_price)} por {brl(price.promotional_price)} "
        f"({price.promotion.discount_percent}% de desconto - {price.promotion.campaign})"
    )


def _price_block(ctx: ToolContext, product: Product) -> dict:
    price = pricing.unit_price(product, ctx.store.promotions.get(product.id))
    block = {"list_price": brl(price.list_price)}
    if price.promotion:
        block["promotion"] = {
            "campaign": price.promotion.campaign,
            "discount_percent": price.promotion.discount_percent,
            "promotional_price": brl(price.promotional_price),
            "present_as": _promotion_text(price),
        }
        block["pix_price"] = None
        block["note"] = "Promotions are not cumulative: no extra PIX discount on the promotional price."
    else:
        block["promotion"] = "nenhuma promoção ativa"
        block["pix_price"] = brl(price.pix_price)
    return block


def _summary(ctx: ToolContext, product: Product) -> dict:
    summary = {
        "product_id": product.id,
        "name": product.name,
        "category": ctx.store.categories[product.category_id].name,
        "availability": product.availability,
        "availability_label": AVAILABILITY_LABELS[product.availability],
        "price": _price_block(ctx, product),
    }
    if product.availability == "available":
        summary["stock"] = product.stock
    return summary


def _details(ctx: ToolContext, product: Product) -> dict:
    details = _summary(ctx, product)
    price = pricing.unit_price(product, ctx.store.promotions.get(product.id))
    card_price = price.promotional_price or price.list_price
    count = pricing.max_installments(card_price)
    details["price"]["credit_card"] = {
        "max_interest_free_installments": count,
        "installment_value": brl(pricing.money(card_price / count)),
    }
    details["description"] = product.description or "(description unavailable)"
    details["specs"] = product.specs
    if product.availability != "available":
        details["alternatives"] = [_summary(ctx, p) for p in ctx.store.alternatives(product)]
    return details


def _date(value: date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value else None


def _payment_label(method: str) -> str:
    match = re.fullmatch(r"credit_(\d+)x", method)
    if match:
        return f"cartão de crédito em {match.group(1)}x"
    return {"pix": "PIX", "debit": "cartão de débito", "boleto": "boleto bancário"}.get(method, method)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _phone_matches(given: str, registered: str) -> bool:
    """Tolerates a missing area code or a +55 prefix, but needs at least 8 digits."""
    a, b = _digits(given), _digits(registered)
    return len(a) >= 8 and (b.endswith(a) or a.endswith(b))


# --- handlers ------------------------------------------------------------------------


def search_products(ctx: ToolContext, args: dict) -> dict:
    category = None
    if "category" in args:
        category = ctx.store.category_by_name(args["category"])
        if not any(p.category_id == category.id for p in ctx.store.products.values()):
            return _envelope(
                "not_found",
                f"The category '{category.name}' currently has no products in the catalog.",
            )
    products = ctx.store.search(
        query=args.get("query"),
        category_id=category.id if category else None,
        min_price=Decimal(str(args["min_price"])) if "min_price" in args else None,
        max_price=Decimal(str(args["max_price"])) if "max_price" in args else None,
        string_type=args.get("string_type"),
        electric=args.get("electric"),
        # A customer naming a product must hear its real status, so name searches include
        # unavailable matches (flagged); browsing without a query shows only what can be bought.
        include_unavailable=args.get("include_unavailable", "query" in args),
    )
    if not products:
        return _envelope(
            "not_found",
            "No products match these filters. Try fewer query words or relax a filter; "
            "set include_unavailable=true to also see out-of-stock/discontinued items.",
        )
    limit = args.get("limit", 8)
    shown = []
    for product in products[:limit]:
        summary = _summary(ctx, product)
        if product.availability != "available":
            # Manual §7.3: an unavailable product comes with similar available alternatives.
            summary["alternatives"] = [_summary(ctx, p) for p in ctx.store.alternatives(product)]
        shown.append(summary)
    return _envelope(
        "ok",
        data={
            "total_found": len(products),
            "shown": len(shown),
            "price_filters_use": "list price",
            "products": shown,
        },
    )


def _find_product(ctx: ToolContext, ref: dict) -> tuple[Product | None, dict | None]:
    """Resolve {"product_id"} or {"name"} to a product, or return the envelope explaining why not."""
    if "product_id" in ref:
        product = ctx.store.products.get(ref["product_id"])
        if not product:
            return None, _envelope("not_found", f"There is no product with id {ref['product_id']}.")
        return product, None
    if "name" not in ref:
        raise InvalidInput("provide product_id or name")
    matches = ctx.store.resolve_product(ref["name"])
    if not matches:
        return None, _envelope("not_found", f"No product in the catalog matches '{ref['name']}'.")
    if len(matches) > 1:
        return None, _envelope(
            "ambiguous",
            f"Several products match '{ref['name']}'; ask the customer which one they mean.",
            data={"candidates": [_summary(ctx, p) for p in matches]},
        )
    return matches[0], None


def get_product_details(ctx: ToolContext, args: dict) -> dict:
    product, problem = _find_product(ctx, args)
    return problem or _envelope("ok", data=_details(ctx, product))


def quote_purchase(ctx: ToolContext, args: dict) -> dict:
    lines = []
    for item in args["items"]:
        product, problem = _find_product(ctx, item)
        if problem:
            return problem
        if product.availability != "available":
            raise InvalidInput(f"{product.name} cannot be purchased right now (availability: {product.availability})")
        if item["quantity"] > product.stock:
            raise InvalidInput(f"only {product.stock} unit(s) of {product.name} in stock")
        lines.append((product, item["quantity"], ctx.store.promotions.get(product.id)))

    q = pricing.quote(lines, args["payment_method"], args.get("installments", 1), args.get("delivery_city"))
    data = {
        "items": [
            {
                "product_id": l.product.id,
                "name": l.product.name,
                "quantity": l.quantity,
                "list_unit_price": brl(l.price.list_price),
                "promotion": (
                    {"campaign": l.price.promotion.campaign, "discount_percent": l.price.promotion.discount_percent,
                     "present_as": _promotion_text(l.price)}
                    if l.price.promotion else None
                ),
                "unit_price_charged": brl(l.unit_charged),
                "line_total": brl(l.line_total),
            }
            for l in q.lines
        ],
        "list_total": brl(q.list_total),
        "promotion_discount": brl(q.promotion_discount),
        "pix_discount": brl(q.pix_discount),
        "items_total": brl(q.items_total),
    }
    if q.shipping_fee is not None:
        data["shipping"] = {"city": "Campo Grande", "fee": brl(q.shipping_fee)}
        data["total"] = brl(q.total)
    else:
        data["shipping"] = {
            "fee": None,
            "note": "Freight outside Campo Grande (or with no city informed) is calculated by CEP — see shipping policy.",
        }
        data["total"] = None
    payment = {"method": q.payment_method, "installments": q.installments}
    if q.payment_method == "credit":
        payment["installments_valid"] = q.installments_valid
        payment["max_interest_free_installments"] = q.max_installments
        if q.installments_valid:
            payment["installment_value"] = brl(q.installment_value)
    data["payment"] = payment
    return _envelope("ok", data=data)


def get_order_status(ctx: ToolContext, args: dict) -> dict:
    order = ctx.store.orders.get(args["order_id"])
    customer = ctx.store.customers.get(order.customer_id) if order else None
    if not order or not _phone_matches(args["phone"], customer.phone):
        # Same answer whether the order is missing or the phone is wrong: no enumeration.
        return _envelope(
            "not_found",
            "No order matches this order number and phone. Ask the customer to double-check both.",
        )
    eta = order.estimated_delivery
    data = {
        "order_id": order.id,
        "customer_first_name": customer.name.split()[0],
        "status": order.status,
        "status_label": ORDER_STATUS_LABELS[order.status],
        "order_date": _date(order.order_date),
        "items": [
            {"name": ctx.store.products[i.product_id].name, "quantity": i.quantity} for i in order.items
        ],
        "total": brl(order.total),
        "payment_method": _payment_label(order.payment_method),
        "tracking_code": order.tracking_code,
        "estimated_delivery": _date(eta),
        "notes": order.notes,
        "facts": {
            "reference_date": _date(ctx.reference_date),
            "days_since_order": (ctx.reference_date - order.order_date).days,
            "days_since_estimated_delivery": (ctx.reference_date - eta).days if eta else None,
            "is_late": order.status == "shipped" and eta is not None and ctx.reference_date > eta,
            "receipt_date_note": (
                "The actual delivery date is not recorded; the estimated delivery date is the best "
                "available proxy for when the customer received the order."
            ),
        },
    }
    if order.status == "delivered":
        # Questions about delivered orders are almost always about returns, exchanges or warranty.
        # The deadline arithmetic is done here (the local model got it wrong); the policy text comes
        # along so the model can explain conditions and next steps in the manual's own terms.
        elapsed = {"receipt": data["facts"]["days_since_estimated_delivery"], "purchase": data["facts"]["days_since_order"]}
        data["deadline_check"] = {
            name: {
                "limit_days": limit,
                "counted_from": "recebimento (data estimada)" if anchor == "receipt" else "compra",
                "days_elapsed": elapsed[anchor],
                "within_deadline": elapsed[anchor] <= limit,
            }
            for name, limit, anchor, _, _ in DEADLINES
        }
        data["deadlines_still_open"] = [n for n, c in data["deadline_check"].items() if c["within_deadline"]]
        data["deadlines_expired"] = [n for n, c in data["deadline_check"].items() if not c["within_deadline"]]
        data["policies_for_this_order"] = {s: ctx.policies[s] for s in ("returns_and_exchanges", "warranty")}
        data["how_to_apply"] = (
            "deadline_check says which deadlines are still open. Explain them with the policy text. When a deadline "
            "has passed, tell the customer what is still open (deadlines_still_open) and what the policy says to do "
            "next (e.g. manufacturer warranty, which the store can intermediate). Offer nothing the policy does not "
            "mention. Do not compute dates."
        )
    return _envelope("ok", data=data)


def get_policy(ctx: ToolContext, args: dict) -> dict:
    return _envelope("ok", data={section: ctx.policies[section] for section in args["sections"]})


def escalate_to_human(ctx: ToolContext, args: dict) -> dict:
    existing = ctx.handoffs_path.read_text(encoding="utf-8").splitlines() if ctx.handoffs_path.exists() else []
    protocol = f"ATD-{ctx.reference_date:%Y%m%d}-{len(existing) + 1:03d}"
    record = {"protocol": protocol, "created_at": datetime.now().isoformat(timespec="seconds"), **args}
    with open(ctx.handoffs_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return _envelope(
        "ok",
        data={"protocol": protocol, "response_deadline": "até 24 horas úteis (manual, seção 7.3)"},
    )


# --- registry ------------------------------------------------------------------------


def build_tools(store: Store) -> list[Tool]:
    categories = [c.name for c in sorted(store.categories.values(), key=lambda c: c.id)]
    return [
        Tool(
            "search_products",
            "Search the store catalog. Use it to browse or recommend products and to answer which products "
            "exist in a category, price range, string type (nylon/steel) or with a pickup (eletroacústico). "
            "Without a query only products available for purchase are returned; with a query, unavailable "
            "matches are included and flagged in 'availability'. Price filters use the list price. "
            "For a specific model the customer names, prefer get_product_details.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text words, e.g. brand, model or 'iniciante'."},
                    "category": {"type": "string", "enum": categories},
                    "min_price": {"type": "number", "minimum": 0},
                    "max_price": {"type": "number", "minimum": 0},
                    "string_type": {"type": "string", "enum": ["nylon", "steel"],
                                    "description": "Violões/ukuleles only: nylon or steel (aço) strings."},
                    "electric": {"type": "boolean",
                                 "description": "Violões/ukuleles only: true = eletroacústico (has a pickup)."},
                    "include_unavailable": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            },
            search_products,
        ),
        Tool(
            "get_product_details",
            "Look up one specific product by id or by the name the customer used: price, active promotion, "
            "PIX price, maximum interest-free installments, availability, stock, description and specs. "
            "If it is not available, similar available alternatives are included. If the name is ambiguous, "
            "candidates are returned: ask the customer which one they mean.",
            {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
            get_product_details,
        ),
        Tool(
            "quote_purchase",
            "Compute the exact amount for buying one or more products with a payment method: promotions, "
            "PIX discount (never on promotional items), installment validity and the Campo Grande delivery fee. "
            "Use it whenever the customer asks how much they would pay with a payment method, in installments, "
            "for several items or with delivery.",
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array", "minItems": 1, "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": {"type": "integer",
                                               "description": "Only an id returned by another tool."},
                                "name": {"type": "string",
                                         "description": "The product name as the customer said it (instead of product_id)."},
                                "quantity": {"type": "integer", "minimum": 1, "maximum": 10},
                            },
                            "required": ["quantity"],
                        },
                    },
                    "payment_method": {"type": "string", "enum": list(pricing.PAYMENT_METHODS)},
                    "installments": {"type": "integer", "minimum": 1, "maximum": 12,
                                     "description": "Credit card only."},
                    "delivery_city": {"type": "string",
                                      "description": "Only if the customer said where to deliver."},
                },
                "required": ["items", "payment_method"],
            },
            quote_purchase,
        ),
        Tool(
            "get_order_status",
            "Look up an order. Requires the order number AND the phone number registered on the order — "
            "ask the customer for both before calling. A mismatch returns not_found (never reveal whether the "
            "order exists). Returns status, items, total and tracking code; for delivered orders also a deadline "
            "check and the return/warranty policies to explain it.",
            {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                    "phone": {"type": "string"},
                },
                "required": ["order_id", "phone"],
            },
            get_order_status,
        ),
        Tool(
            "get_policy",
            "Read sections of the store policy manual (verbatim, in Portuguese). Use it for any question about "
            "store rules or store information: contact and address, opening hours, payments, returns and "
            "exchanges, shipping, promotion rules, warranty, privacy. Read the relevant section before stating "
            "any policy.",
            {
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "array", "minItems": 1, "maxItems": 3,
                        "items": {"type": "string", "enum": list(SECTION_SUMMARIES)},
                    },
                },
                "required": ["sections"],
            },
            get_policy,
        ),
        Tool(
            "escalate_to_human",
            "Forward the case to the store's human team and get a protocol number. Use it for complaints, late "
            "or problematic orders, return/exchange/warranty requests, cancellation requests, doubts about how a "
            "policy applies, or when the customer asks to talk to a person. Write the summary in Portuguese.",
            {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "enum": ESCALATION_REASONS},
                    "summary": {"type": "string"},
                    "order_id": {"type": "integer"},
                    "customer_contact": {"type": "string"},
                },
                "required": ["reason", "summary"],
            },
            escalate_to_human,
        ),
    ]


class ToolRegistry:
    def __init__(self, tools: list[Tool], context: ToolContext):
        self.tools = {t.name: t for t in tools}
        self.context = context

    def run(self, name: str, args: dict) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return _envelope("error", f"Unknown tool '{name}'.")
        # Small models misspell optional arguments (e.g. "include_unavailability"); rejecting the
        # whole call made them give up, so unknown top-level arguments are dropped with a warning.
        known = tool.input_schema.get("properties", {})
        ignored = sorted(set(args) - set(known))
        args = {k: v for k, v in args.items() if k in known}
        try:
            _validate(args, tool.input_schema)
            result = tool.handler(self.context, args)
            if ignored:
                result["warning"] = f"Ignored unknown arguments {ignored}; valid arguments are {sorted(known)}."
            return result
        except InvalidInput as e:
            return _envelope("invalid_input", str(e))
        except Exception:
            log.exception("Tool %s failed with args %s", name, args)
            return _envelope("error", "Internal error while running the tool. Do not guess the answer.")

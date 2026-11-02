import json
from datetime import date

import pytest

from emporio.config import PROJECT_ROOT
from emporio.policies import load_policies
from emporio.tools import ToolContext, ToolRegistry, build_tools

ORDER_8_PHONE = "(67) 99654-7821"  # customer 2
ORDER_7_PHONE = "(67) 99789-0123"  # customer 14


@pytest.fixture
def registry(store, tmp_path):
    context = ToolContext(
        store=store,
        policies=load_policies(PROJECT_ROOT / "data" / "policies.md"),
        reference_date=date(2026, 3, 25),
        handoffs_path=tmp_path / "handoffs.jsonl",
    )
    return ToolRegistry(build_tools(store), context)


def test_search_with_category_and_price(registry):
    result = registry.run("search_products", {"category": "Violões", "max_price": 1000, "limit": 20})
    assert result["status"] == "ok"
    assert result["data"]["total_found"] == 12


def test_name_search_includes_unavailable_matches(registry):
    products = registry.run("search_products", {"query": "Giannini GF-3D"})["data"]["products"]
    assert products[0]["product_id"] == 96 and products[0]["availability"] == "out_of_stock"
    browsing = registry.run("search_products", {"category": "Violões", "limit": 20})["data"]["products"]
    assert all(p["availability"] == "available" for p in browsing)


def test_search_empty_category_says_so(registry):
    result = registry.run("search_products", {"category": "Instrumentos de Sopro (Madeiras)"})
    assert result["status"] == "not_found"
    assert "no products" in result["message"]


def test_product_price_without_promotion(registry):
    data = registry.run("get_product_details", {"name": "Takamine GD20"})["data"]
    assert data["product_id"] == 95
    assert data["price"]["list_price"] == "R$ 2.199,00"
    assert data["price"]["pix_price"] == "R$ 2.089,05"
    assert data["price"]["promotion"] == "nenhuma promoção ativa"
    assert data["price"]["credit_card"] == {"max_interest_free_installments": 12, "installment_value": "R$ 183,25"}


def test_product_with_active_promotion(registry):
    price = registry.run("get_product_details", {"product_id": 94})["data"]["price"]
    assert price["promotion"] == {"campaign": "Desconto Exclusivo", "discount_percent": 8, "promotional_price": "R$ 5.519,08"}
    assert price["pix_price"] is None


def test_out_of_stock_product_has_alternatives(registry):
    data = registry.run("get_product_details", {"product_id": 96})["data"]
    assert data["availability"] == "out_of_stock"
    assert data["availability_label"].startswith("esgotado")
    assert "stock" not in data
    assert data["alternatives"]


def test_ambiguous_product_name(registry):
    result = registry.run("get_product_details", {"name": "Taylor"})
    assert result["status"] == "ambiguous"
    assert {c["product_id"] for c in result["data"]["candidates"]} == {94, 106}


def test_order_status_with_late_delivery(registry):
    data = registry.run("get_order_status", {"order_id": 8, "phone": ORDER_8_PHONE})["data"]
    assert data["status"] == "shipped"
    assert data["tracking_code"] == "BRJL5544332BR"
    assert data["facts"]["days_since_estimated_delivery"] == 25
    assert data["facts"]["is_late"] is True


def test_order_facts_for_return_windows(registry):
    facts = registry.run("get_order_status", {"order_id": 7, "phone": ORDER_7_PHONE})["data"]["facts"]
    assert facts["days_since_estimated_delivery"] == 36
    assert facts["days_since_order"] == 50


def test_wrong_phone_and_missing_order_look_identical(registry):
    wrong_phone = registry.run("get_order_status", {"order_id": 8, "phone": "(67) 90000-0000"})
    missing = registry.run("get_order_status", {"order_id": 999, "phone": ORDER_8_PHONE})
    assert wrong_phone == missing
    assert wrong_phone["status"] == "not_found"


@pytest.mark.parametrize("phone, ok", [("99654-7821", True), ("+55 67 99654 7821", True), ("7821", False)])
def test_phone_matching_tolerance(registry, phone, ok):
    result = registry.run("get_order_status", {"order_id": 8, "phone": phone})
    assert (result["status"] == "ok") is ok


def test_quote_rejects_unavailable_product(registry):
    result = registry.run("quote_purchase", {"items": [{"product_id": 96, "quantity": 1}], "payment_method": "pix"})
    assert result["status"] == "invalid_input"


def test_quote_rejects_quantity_above_stock(registry):
    result = registry.run("quote_purchase", {"items": [{"product_id": 93, "quantity": 3}], "payment_method": "pix"})
    assert result["status"] == "invalid_input"
    assert "only 2" in result["message"]


def test_quote_with_campo_grande_delivery(registry):
    data = registry.run(
        "quote_purchase",
        {"items": [{"product_id": 83, "quantity": 1}], "payment_method": "credit", "installments": 3,
         "delivery_city": "Campo Grande"},
    )["data"]
    assert data["shipping"]["fee"] == "R$ 35,00"
    assert data["total"] == "R$ 464,90"
    assert data["payment"]["installment_value"] == "R$ 154,97"


def test_quote_accepts_product_names(registry):
    data = registry.run(
        "quote_purchase",
        {"items": [{"name": "Yamaha C40", "quantity": 1}, {"name": "Kala KA-15S", "quantity": 1}],
         "payment_method": "pix", "delivery_city": "Campo Grande"},
    )["data"]
    assert data["total"] == "R$ 759,82"


def test_quote_with_ambiguous_name_asks_which(registry):
    result = registry.run("quote_purchase", {"items": [{"name": "Taylor", "quantity": 1}], "payment_method": "pix"})
    assert result["status"] == "ambiguous"


def test_deadline_check_for_delivered_orders(registry):
    checks = registry.run("get_order_status", {"order_id": 7, "phone": ORDER_7_PHONE})["data"]["deadline_check"]
    assert checks["direito_de_arrependimento"] == {
        "limit_days": 7, "counted_from": "recebimento (data estimada)", "days_elapsed": 36, "within_deadline": False,
    }
    assert checks["troca_por_defeito"]["within_deadline"] is False  # 50 days since purchase
    assert checks["garantia_legal"]["within_deadline"] is True
    boundary = registry.run("get_order_status", {"order_id": 4, "phone": "(67) 99812-3456"})["data"]["deadline_check"]
    assert boundary["garantia_legal"]["days_elapsed"] == 93
    assert boundary["garantia_legal"]["within_deadline"] is False


def test_delivered_order_includes_return_and_warranty_policies(registry):
    data = registry.run("get_order_status", {"order_id": 7, "phone": ORDER_7_PHONE})["data"]
    assert set(data["policies_for_this_order"]) == {"returns_and_exchanges", "warranty"}
    assert "7 (sete) dias" in data["policies_for_this_order"]["returns_and_exchanges"]
    shipped = registry.run("get_order_status", {"order_id": 8, "phone": ORDER_8_PHONE})["data"]
    assert "policies_for_this_order" not in shipped


def test_get_policy_returns_requested_sections(registry):
    data = registry.run("get_policy", {"sections": ["opening_hours", "warranty"]})["data"]
    assert set(data) == {"opening_hours", "warranty"}
    assert "09:00 às 18:00" in data["opening_hours"]


@pytest.mark.parametrize(
    "name, args",
    [
        ("get_policy", {"sections": ["refunds"]}),  # not in enum
        ("get_order_status", {"order_id": 8}),  # missing phone
        ("get_order_status", {"order_id": "8", "phone": ORDER_8_PHONE}),  # wrong type
        ("get_product_details", {"product_id": True}),  # bool is not an integer
        ("get_product_details", {}),  # neither id nor name
    ],
)
def test_invalid_input(registry, name, args):
    assert registry.run(name, args)["status"] == "invalid_input"


def test_unknown_arguments_are_ignored_with_a_warning(registry):
    result = registry.run("search_products", {"query": "Kala KA-ABP-CTG", "include_unavailability": True})
    assert result["status"] == "ok"
    assert "include_unavailability" in result["warning"]
    assert result["data"]["products"][0]["product_id"] == 130


def test_unknown_tool(registry):
    assert registry.run("delete_database", {})["status"] == "error"


def test_handler_exception_becomes_error_envelope(registry, monkeypatch):
    monkeypatch.setattr(registry.context, "store", None)
    result = registry.run("get_product_details", {"product_id": 1})
    assert result["status"] == "error"


def test_escalation_writes_handoff_with_sequential_protocol(registry):
    args = {"reason": "complaint", "summary": "Cliente insatisfeito com atraso", "order_id": 8}
    first = registry.run("escalate_to_human", args)["data"]["protocol"]
    second = registry.run("escalate_to_human", args)["data"]["protocol"]
    assert (first, second) == ("ATD-20260325-001", "ATD-20260325-002")
    lines = registry.context.handoffs_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["order_id"] == 8

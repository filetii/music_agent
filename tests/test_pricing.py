from decimal import Decimal

import pytest

from emporio.pricing import brl, installments_allowed, max_installments, quote, unit_price


def lines(store, *items):
    return [(store.products[pid], qty, store.promotions.get(pid)) for pid, qty in items]


@pytest.mark.parametrize(
    "value, expected",
    [("2089.05", "R$ 2.089,05"), ("11499", "R$ 11.499,00"), ("599.9", "R$ 599,90"), ("35", "R$ 35,00")],
)
def test_brl_format(value, expected):
    assert brl(Decimal(value)) == expected


def test_pix_discount_without_promotion(store):
    price = unit_price(store.products[95], store.promotions.get(95))  # Takamine GD20, promotion inactive
    assert price.list_price == Decimal("2199")
    assert price.promotion is None
    assert price.pix_price == Decimal("2089.05")


def test_promotion_is_not_combined_with_pix(store):
    price = unit_price(store.products[94], store.promotions.get(94))  # Taylor 110e, 8% active
    assert price.promotional_price == Decimal("5519.08")
    assert price.pix_price is None
    q = quote(lines(store, (94, 1)), "pix")
    assert q.items_total == Decimal("5519.08")
    assert q.pix_discount == 0


@pytest.mark.parametrize(
    "total, expected",
    [("2199", 12), ("349.9", 4), ("159.9", 3), ("80", 1), ("1000", 10)],
)
def test_max_installments_follow_policy_tiers(total, expected):
    assert max_installments(Decimal(total)) == expected


def test_installment_tier_boundaries():
    assert installments_allowed(Decimal("150"), 3)  # 50.00 per installment: allowed
    assert not installments_allowed(Decimal("149.99"), 3)
    assert installments_allowed(Decimal("480"), 6)  # 80.00
    assert not installments_allowed(Decimal("699"), 7)  # 99.86 < 100


def test_cart_with_pix_and_free_campo_grande_delivery(store):
    q = quote(lines(store, (81, 1), (116, 1)), "pix", delivery_city="Campo Grande")
    assert q.list_total == Decimal("799.80")
    assert q.items_total == Decimal("759.82")
    assert q.shipping_fee == 0
    assert q.total == Decimal("759.82")


def test_mixed_cart_pix_only_on_non_promotional_items(store):
    q = quote(lines(store, (94, 1), (81, 1)), "pix")
    assert q.items_total == Decimal("5519.08") + Decimal("569.91")
    assert q.promotion_discount == Decimal("479.92")
    assert q.pix_discount == Decimal("29.99")


def test_campo_grande_fee_below_threshold(store):
    q = quote(lines(store, (83, 1)), "debit", delivery_city="campo grande")
    assert q.shipping_fee == Decimal("35")
    assert q.total == Decimal("464.90")


def test_other_cities_need_a_cep_quote(store):
    q = quote(lines(store, (83, 1)), "debit", delivery_city="Dourados")
    assert q.shipping_fee is None and q.total is None


def test_credit_installments(store):
    q = quote(lines(store, (95, 1)), "credit", installments=10)
    assert q.installments_valid and q.installment_value == Decimal("219.90")
    q = quote(lines(store, (120, 1)), "credit", installments=12)
    assert not q.installments_valid and q.max_installments == 4


def test_non_credit_payment_ignores_installments(store):
    q = quote(lines(store, (95, 1)), "boleto", installments=10)
    assert q.installments == 1 and q.items_total == Decimal("2199")

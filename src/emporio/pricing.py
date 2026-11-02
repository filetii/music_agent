"""Deterministic price math. The LLM never computes prices; it only reads these results.

Rules come from the store policy manual:
- §3: PIX gives 5% off the list price; credit card up to 12x interest-free.
- §3.1: minimum installment per tier (up to 3x: R$50 - read from the ambiguous
  "sem valor mínimo (exceto abaixo de R$ 50,00)"; 4-6x: R$80; 7-12x: R$100).
- §5.1: Campo Grande delivery is free above R$500, otherwise R$35.
- §6.2: promotions are not cumulative - no PIX discount on promotional prices.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .data import Product, Promotion, normalize

CENT = Decimal("0.01")
PIX_DISCOUNT = Decimal("0.05")
METRO_FREE_SHIPPING_ABOVE = Decimal("500")
METRO_SHIPPING_FEE = Decimal("35")
# (highest installment count in the tier, minimum installment value)
INSTALLMENT_TIERS = ((3, Decimal("50")), (6, Decimal("80")), (12, Decimal("100")))
PAYMENT_METHODS = ("pix", "debit", "credit", "boleto")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def brl(value: Decimal) -> str:
    """Brazilian currency format: Decimal('2089.05') -> 'R$ 2.089,05'."""
    whole, cents = f"{money(value):,.2f}".split(".")
    return f"R$ {whole.replace(',', '.')},{cents}"


@dataclass(frozen=True)
class UnitPrice:
    list_price: Decimal
    promotion: Promotion | None
    promotional_price: Decimal | None
    pix_price: Decimal | None  # None when a promotion applies (not cumulative)


def unit_price(product: Product, promotion: Promotion | None) -> UnitPrice:
    if promotion:
        discounted = money(product.price * (100 - promotion.discount_percent) / 100)
        return UnitPrice(product.price, promotion, discounted, None)
    return UnitPrice(product.price, None, None, money(product.price * (1 - PIX_DISCOUNT)))


def minimum_installment(count: int) -> Decimal:
    return next(minimum for highest, minimum in INSTALLMENT_TIERS if count <= highest)


def installments_allowed(total: Decimal, count: int) -> bool:
    if count == 1:
        return True
    return 1 < count <= 12 and total >= minimum_installment(count) * count


def max_installments(total: Decimal) -> int:
    return max(n for n in range(1, 13) if installments_allowed(total, n))


@dataclass(frozen=True)
class QuoteLine:
    product: Product
    quantity: int
    price: UnitPrice
    unit_charged: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class Quote:
    lines: tuple[QuoteLine, ...]
    list_total: Decimal
    promotion_discount: Decimal
    pix_discount: Decimal
    items_total: Decimal
    shipping_fee: Decimal | None  # None: must be quoted by CEP (outside Campo Grande or no city given)
    total: Decimal | None
    payment_method: str
    installments: int
    installment_value: Decimal | None
    installments_valid: bool
    max_installments: int


def quote(
    lines: list[tuple[Product, int, Promotion | None]],
    payment_method: str,
    installments: int = 1,
    delivery_city: str | None = None,
) -> Quote:
    if payment_method not in PAYMENT_METHODS:
        raise ValueError(f"payment_method must be one of {PAYMENT_METHODS}")
    if payment_method != "credit":
        installments = 1

    quote_lines = []
    for product, quantity, promotion in lines:
        price = unit_price(product, promotion)
        if price.promotional_price is not None:
            charged = price.promotional_price
        elif payment_method == "pix":
            charged = price.pix_price
        else:
            charged = price.list_price
        quote_lines.append(QuoteLine(product, quantity, price, charged, money(charged * quantity)))

    list_total = money(sum((l.price.list_price * l.quantity for l in quote_lines), Decimal(0)))
    items_total = money(sum((l.line_total for l in quote_lines), Decimal(0)))
    promotion_discount = money(sum(
        ((l.price.list_price - l.price.promotional_price) * l.quantity
         for l in quote_lines if l.price.promotional_price is not None),
        Decimal(0),
    ))
    pix_discount = list_total - promotion_discount - items_total

    if delivery_city and normalize(delivery_city) == "campo grande":
        shipping_fee = Decimal(0) if items_total > METRO_FREE_SHIPPING_ABOVE else METRO_SHIPPING_FEE
    else:
        shipping_fee = None
    total = items_total + shipping_fee if shipping_fee is not None else None

    payable = total if total is not None else items_total
    valid = installments_allowed(payable, installments)
    return Quote(
        lines=tuple(quote_lines),
        list_total=list_total,
        promotion_discount=promotion_discount,
        pix_discount=pix_discount,
        items_total=items_total,
        shipping_fee=shipping_fee,
        total=total,
        payment_method=payment_method,
        installments=installments,
        installment_value=money(payable / installments) if valid else None,
        installments_valid=valid,
        max_installments=max_installments(payable) if payment_method == "credit" else 1,
    )

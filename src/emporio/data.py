"""Load the store's CSV exports into validated, immutable objects and query them.

All data is small (tens of rows), so it is loaded once into memory. Loading fails
fast on integrity problems (duplicate ids, broken references, unknown statuses)
instead of letting the agent answer from inconsistent data.
"""

import csv
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from rapidfuzz import fuzz

log = logging.getLogger(__name__)

PRODUCT_STATUSES = {"active", "discontinued", "coming_soon"}
ORDER_STATUSES = {"pending", "confirmed", "shipped", "delivered", "cancelled"}

# Brands that appear in product names or descriptions. A description that names a
# brand absent from the product's own name contradicts it (rows 135-144 of the
# export), so such descriptions are not shown to the customer.
KNOWN_BRANDS = [
    "Crafter", "Fender", "Giannini", "Gibson", "Ibanez", "Kala", "Kalani", "Korg",
    "Martin", "Music Man", "Nord", "Ohana", "Pearl", "PRS", "Roland", "Rozini",
    "Shelby", "Tagima", "Takamine", "Tama", "Taylor", "Yamaha",
]

STOPWORDS = {"a", "as", "o", "os", "de", "da", "do", "das", "dos", "e", "em", "um", "uma", "para", "pra", "com"}


class DataError(Exception):
    """The CSV exports are inconsistent in a way the agent cannot work around."""


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation: 'Violão Elétrico' -> 'violao eletrico'."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _stem(token: str) -> str:
    """Crude Portuguese plural folding so 'violoes' matches 'violao'."""
    for suffix, replacement in (("oes", "ao"), ("aes", "ao"), ("es", ""), ("s", "")):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)] + replacement
    return token


def tokens(text: str) -> list[str]:
    return [_stem(t) for t in normalize(text).split() if t not in STOPWORDS]


def _token_similarity(query: str, candidate: str) -> float:
    if query == candidate:
        return 100.0
    if len(query) >= 3 and len(candidate) >= 3 and (candidate.startswith(query) or query.startswith(candidate)):
        return 90.0
    ratio = fuzz.ratio(query, candidate)
    return ratio if ratio >= 80 else 0.0


def _match(query_tokens: list[str], fields: list[tuple[list[str], float]]) -> tuple[int, float]:
    """How many query tokens match the weighted token fields, and a relevance score."""
    matched, score = 0, 0.0
    for q in query_tokens:
        best = max((_token_similarity(q, t) * weight for toks, weight in fields for t in toks), default=0.0)
        if best:
            matched += 1
            score += best
    return matched, score


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    description: str


@dataclass(frozen=True)
class Promotion:
    id: int
    product_id: int
    discount_percent: int
    campaign: str


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    category_id: int
    price: Decimal
    description: str | None  # None when it contradicts the product name
    stock: int
    status: str
    specs: dict
    created_at: date

    @property
    def availability(self) -> str:
        if self.status == "discontinued":
            return "discontinued"
        if self.status == "coming_soon":
            return "coming_soon"
        return "available" if self.stock > 0 else "out_of_stock"

    @property
    def string_type(self) -> str | None:
        """'nylon' or 'steel' for violões/ukuleles; None where specs.strings is a string count."""
        value = self.specs.get("strings")
        return value if value in ("nylon", "steel") else None

    @property
    def electric(self) -> bool | None:
        """Whether a violão/ukulele has a pickup (eletroacústico); None when not applicable."""
        value = self.specs.get("electronics")
        return {"yes": True, "no": False}.get(value)


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    quantity: int


@dataclass(frozen=True)
class Order:
    id: int
    customer_id: int
    order_date: date
    status: str
    total: Decimal  # authoritative: product prices are current, not historical
    payment_method: str
    tracking_code: str | None
    estimated_delivery: date | None
    notes: str | None
    items: tuple[OrderItem, ...]


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    phone: str


class Store:
    def __init__(self, categories, products, promotions, orders, customers):
        self.categories: dict[int, Category] = categories
        self.products: dict[int, Product] = products
        self.promotions: dict[int, Promotion] = promotions  # active promotion per product id
        self.orders: dict[int, Order] = orders
        self.customers: dict[int, Customer] = customers

    def category_by_name(self, name: str) -> Category | None:
        wanted = normalize(name)
        return next((c for c in self.categories.values() if normalize(c.name) == wanted), None)

    def _fields(self, product: Product, with_description: bool) -> list[tuple[list[str], float]]:
        fields = [(tokens(product.name), 1.0), (tokens(self.categories[product.category_id].name), 0.8)]
        if with_description and product.description:
            fields.append((tokens(product.description), 0.6))
        return fields

    def search(
        self,
        query: str | None = None,
        category_id: int | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        string_type: str | None = None,
        electric: bool | None = None,
        include_unavailable: bool = False,
    ) -> list[Product]:
        """Filter the catalog; with a query, keep only the best-matching tier of products."""
        candidates = [
            p for p in self.products.values()
            if (category_id is None or p.category_id == category_id)
            and (min_price is None or p.price >= min_price)
            and (max_price is None or p.price <= max_price)
            and (string_type is None or p.string_type == string_type)
            and (electric is None or p.electric == electric)
            and (include_unavailable or p.availability == "available")
        ]
        query_tokens = tokens(query) if query else []
        if not query_tokens:
            return sorted(candidates, key=lambda p: p.price)
        scored = [(p, *_match(query_tokens, self._fields(p, with_description=True))) for p in candidates]
        scored = [s for s in scored if s[1] > 0]
        if not scored:
            return []
        best = max(s[1] for s in scored)
        return [p for p, _, _ in sorted((s for s in scored if s[1] == best), key=lambda s: (-s[2], s[0].price))]

    def resolve_product(self, name: str) -> list[Product]:
        """Products the name most plausibly refers to: one item if unambiguous, several if not, [] if none."""
        query_tokens = tokens(name)
        scored = [(p, *_match(query_tokens, self._fields(p, with_description=False))) for p in self.products.values()]
        scored = [s for s in scored if s[1] > 0]
        if not scored:
            return []
        best_matched = max(s[1] for s in scored)
        tier = sorted((s for s in scored if s[1] == best_matched), key=lambda s: -s[2])
        top_score = tier[0][2]
        return [p for p, _, score in tier if top_score - score < 15][:5]

    def alternatives(self, product: Product, limit: int = 3) -> list[Product]:
        """Available products in the same category, same string type first, then closest in price."""
        pool = [
            p for p in self.products.values()
            if p.category_id == product.category_id and p.id != product.id and p.availability == "available"
        ]
        return sorted(pool, key=lambda p: (p.string_type != product.string_type, abs(p.price - product.price)))[:limit]


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _unique(rows: list, key: str, table: str, errors: list[str]) -> None:
    seen = set()
    for row in rows:
        if row[key] in seen:
            errors.append(f"{table}: duplicate {key} {row[key]}")
        seen.add(row[key])


def _description(name: str, description: str) -> str | None:
    in_name = {b for b in KNOWN_BRANDS if re.search(rf"\b{re.escape(b)}\b", name, re.IGNORECASE)}
    in_description = {b for b in KNOWN_BRANDS if re.search(rf"\b{re.escape(b)}\b", description, re.IGNORECASE)}
    if in_description - in_name:
        log.warning("Hiding description of %r: it mentions %s", name, sorted(in_description - in_name))
        return None
    return description


def load_store(data_dir: Path) -> Store:
    errors: list[str] = []

    category_rows = _read(data_dir / "categories.csv")
    _unique(category_rows, "category_id", "categories", errors)
    categories = {
        int(r["category_id"]): Category(int(r["category_id"]), r["name"], r["description"]) for r in category_rows
    }

    product_rows = _read(data_dir / "products.csv")
    _unique(product_rows, "product_id", "products", errors)
    products = {}
    for r in product_rows:
        pid = int(r["product_id"])
        if int(r["category_id"]) not in categories:
            errors.append(f"products: {pid} has unknown category {r['category_id']}")
        if r["status"] not in PRODUCT_STATUSES:
            errors.append(f"products: {pid} has unknown status {r['status']!r}")
        try:
            specs = json.loads(r["specs"])
        except json.JSONDecodeError:
            errors.append(f"products: {pid} has invalid specs JSON")
            specs = {}
        products[pid] = Product(
            id=pid,
            name=r["name"].strip(),
            category_id=int(r["category_id"]),
            price=Decimal(r["price_brl"]),
            description=_description(r["name"], r["description"]),
            stock=int(r["stock_quantity"]),
            status=r["status"],
            specs=specs,
            created_at=date.fromisoformat(r["created_at"]),
        )

    promotions: dict[int, Promotion] = {}
    for r in _read(data_dir / "promotions.csv"):
        pid = int(r["product_id"])
        if pid not in products:
            errors.append(f"promotions: {r['promotion_id']} references unknown product {pid}")
            continue
        if r["is_active"] != "1":
            continue
        promo = Promotion(int(r["promotion_id"]), pid, int(r["discount_percent"]), r["description"])
        current = promotions.get(pid)
        if current:
            log.warning("Product %s has more than one active promotion; keeping the highest discount", pid)
        if current is None or promo.discount_percent > current.discount_percent:
            promotions[pid] = promo

    customer_rows = _read(data_dir / "customers.csv")
    _unique(customer_rows, "customer_id", "customers", errors)
    customers = {
        int(r["customer_id"]): Customer(int(r["customer_id"]), r["name"], r["phone"]) for r in customer_rows
    }

    items: dict[int, list[OrderItem]] = {}
    for r in _read(data_dir / "order_items.csv"):
        if int(r["product_id"]) not in products:
            errors.append(f"order_items: order {r['order_id']} references unknown product {r['product_id']}")
        items.setdefault(int(r["order_id"]), []).append(OrderItem(int(r["product_id"]), int(r["quantity"])))

    order_rows = _read(data_dir / "orders.csv")
    _unique(order_rows, "order_id", "orders", errors)
    orders = {}
    for r in order_rows:
        oid = int(r["order_id"])
        if int(r["customer_id"]) not in customers:
            errors.append(f"orders: {oid} references unknown customer {r['customer_id']}")
        if r["status"] not in ORDER_STATUSES:
            errors.append(f"orders: {oid} has unknown status {r['status']!r}")
        if oid not in items:
            errors.append(f"orders: {oid} has no items")
        orders[oid] = Order(
            id=oid,
            customer_id=int(r["customer_id"]),
            order_date=date.fromisoformat(r["order_date"]),
            status=r["status"],
            total=Decimal(r["total_brl"]),
            payment_method=r["payment_method"],
            tracking_code=r["tracking_code"] or None,
            estimated_delivery=date.fromisoformat(r["estimated_delivery"]) if r["estimated_delivery"] else None,
            notes=r["notes"] or None,
            items=tuple(items.get(oid, [])),
        )

    if errors:
        raise DataError("Inconsistent store data:\n  " + "\n  ".join(errors))
    return Store(categories, products, promotions, orders, customers)

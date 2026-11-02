import shutil
from decimal import Decimal

import pytest

from emporio.config import PROJECT_ROOT
from emporio.data import DataError, load_store, normalize


def test_loads_all_tables(store):
    assert len(store.products) == 65
    assert len(store.categories) == 9
    assert len(store.orders) == 20
    assert len(store.customers) == 18


def test_only_active_promotions_are_kept(store):
    assert {pid: p.discount_percent for pid, p in store.promotions.items()} == {90: 18, 94: 8, 121: 20, 127: 10}


@pytest.mark.parametrize(
    "product_id, expected",
    [
        (81, "available"),
        (96, "out_of_stock"),  # active but zero stock
        (113, "discontinued"),
        (136, "discontinued"),  # discontinued even though stock is 13
        (130, "coming_soon"),  # coming soon even though stock is 2
    ],
)
def test_availability_rules(store, product_id, expected):
    assert store.products[product_id].availability == expected


def test_contradictory_descriptions_are_hidden(store):
    hidden = sorted(p.id for p in store.products.values() if p.description is None)
    assert hidden == [135, 137, 139, 140, 142, 144]


def test_search_violoes_up_to_1000_excludes_unavailable(store):
    violoes = store.category_by_name("violoes")
    found = {p.id for p in store.search(category_id=violoes.id, max_price=Decimal("1000"))}
    assert found == {81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 107, 112}
    assert 96 not in found and 113 not in found


def test_search_does_not_match_brand_from_hidden_description(store):
    assert [p.id for p in store.search(query="pearl")] == [140]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Takamine GD20", [95]),
        ("gd20", [95]),
        ("Martin D-28", [93]),
        ("violão Yamaha F310", [87]),
        ("Taylor", [94, 106]),  # ambiguous: both returned
        ("saxofone", []),
    ],
)
def test_resolve_product(store, name, expected):
    assert [p.id for p in store.resolve_product(name)] == expected


def test_alternatives_prefer_same_string_type(store):
    alternatives = store.alternatives(store.products[96])  # steel-string, out of stock
    assert alternatives and all(p.availability == "available" for p in alternatives)
    assert alternatives[0].string_type == "steel"


def test_normalize():
    assert normalize("Violão Elétrico Aço!") == "violao eletrico aco"


def test_inconsistent_data_fails_fast(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(PROJECT_ROOT / "data", data)
    with open(data / "order_items.csv", "a", encoding="utf-8") as f:
        f.write("\n1,1,999\n")  # source file has no trailing newline
    with pytest.raises(DataError, match="unknown product 999"):
        load_store(data)

"""The store policy manual, split into sections the agent requests by id."""

import re
from pathlib import Path

# One-line English summaries; they form the table of contents in the system prompt,
# which is how the model decides which section to request.
SECTION_SUMMARIES = {
    "about_and_contact": "Store identity, address, phone/WhatsApp, e-mail; sells instruments only, no accessories.",
    "opening_hours": "Opening hours on weekdays, Saturdays, Sundays/holidays and special dates.",
    "payment_methods": "Accepted payment methods, PIX discount, installment rules, combined payments.",
    "returns_and_exchanges": "Right of withdrawal, defect and preference exchanges (deadlines, conditions), non-eligible items.",
    "shipping_and_delivery": "Delivery in Campo Grande (fee, deadline), other cities (carriers, deadlines), tracking codes.",
    "promotions": "Seasonal campaigns, non-cumulative rule, stock limits, how promotional prices must be shown.",
    "customer_service_guidelines": "WhatsApp number, tone of voice, scope, standard flow, out of stock/discontinued/expired promotion/complaints.",
    "warranty": "Legal warranty (counted from receipt), manufacturer warranty, what is not covered.",
    "privacy_lgpd": "How personal data is used (LGPD) and data deletion requests.",
    "final_provisions": "Scope of the manual; doubts about applying a policy go to management.",
}

_MARKER = re.compile(r"<!--\s*section:\s*([a-z_]+)\s*-->")


def load_policies(path: Path) -> dict[str, str]:
    """Map section id -> verbatim markdown text."""
    parts = _MARKER.split(path.read_text(encoding="utf-8"))
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}
    if set(sections) != set(SECTION_SUMMARIES):
        raise ValueError(f"policy sections {sorted(sections)} do not match {sorted(SECTION_SUMMARIES)}")
    return sections

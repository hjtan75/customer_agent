"""Thin data-access layer over the static JSON appendices.

Keeping storage behind small interfaces means the rest of the agent never
touches JSON directly. If this ever needed to scale, `OrderStore` /
`ProductCatalog` could be backed by a real database without changing any
caller (the tools call `.find()` / `.search()`, not `json.load`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Search tuning: a hit in the product name matters most, then its tags, then
# the prose description (weight 1).
_NAME_WEIGHT = 4
_TAG_WEIGHT = 2

# Common words carry no signal and only add noise to a catalogue this small.
_STOPWORDS = frozenset(
    """a an and any are as at be can do for from get have i in is it me my
    of on or something that the to want we what with you your""".split()
)

_WORD = re.compile(r"[a-z0-9]+")


def _normalize(value: str) -> str:
    """Lowercase + strip so lookups are forgiving of user formatting."""
    return (value or "").strip().lower().lstrip("#")


def _tokenize(value: str) -> list[str]:
    """Lowercase word tokens, so matching is whole-word rather than substring."""
    return _WORD.findall((value or "").lower())


class OrderStore:
    """Lookup of customer orders by (email, order number)."""

    def __init__(self, path: str | Path):
        raw = json.loads(Path(path).read_text())
        # Index by normalized order number for O(1) lookup.
        self._by_order = {_normalize(o.get("OrderNumber", "")): o for o in raw}
        # Group orders by normalized email, for login validation and per-user
        # listing. The email is the authorization key: a session may only ever
        # touch orders under its own email.
        self._by_email: dict[str, list[dict]] = {}
        for order in raw:
            self._by_email.setdefault(_normalize(order.get("Email", "")), []).append(order)

    def find(self, email: str, order_number: str) -> dict | None:
        """Return the order only if BOTH email and order number match."""
        order = self._by_order.get(_normalize(order_number))
        if order and _normalize(order.get("Email", "")) == _normalize(email):
            return order
        return None

    def is_known_email(self, email: str) -> bool:
        """Whether any order exists for this email — the login stand-in."""
        return _normalize(email) in self._by_email

    def orders_for(self, email: str) -> list[dict]:
        """Every order belonging to this email (may be empty)."""
        return list(self._by_email.get(_normalize(email), []))


class ProductCatalog:
    """The Sierra Outfitters product catalogue."""

    def __init__(self, path: str | Path):
        self._products = json.loads(Path(path).read_text())
        # Index by normalized SKU so orders can resolve SKUs -> product names.
        self._by_sku = {_normalize(p.get("SKU", "")): p for p in self._products}

    def all(self) -> list[dict]:
        return self._products

    def has_sku(self, sku: str) -> bool:
        """Whether this SKU resolves to a real catalog product."""
        return _normalize(sku) in self._by_sku

    def name_for_sku(self, sku: str) -> str:
        """Product name for a SKU, or the raw SKU if it isn't in the catalog."""
        product = self._by_sku.get(_normalize(sku))
        return product.get("ProductName", sku) if product else sku

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Weighted keyword scan over name / tags / description.

        Deliberately basic: the LLM does the reasoning about which results fit
        the customer. For fuzzy/semantic search we'd swap in embeddings, but
        that's overkill for a catalogue this size.

        Matching is whole-word, not substring — counting substrings made short
        terms match inside unrelated words ("me" in "Home Decor"), which pulled
        junk into the results. Name and tag hits outrank description hits, since
        a product named "Backpack" beats one that merely mentions backpacks.
        """
        terms = [t for t in _tokenize(query) if t not in _STOPWORDS]
        if not terms:
            return self._products[:limit]

        scored: list[tuple[int, int, dict]] = []
        for index, product in enumerate(self._products):
            name = _tokenize(product.get("ProductName", ""))
            tags = _tokenize(" ".join(product.get("Tags", []) or []))
            description = _tokenize(product.get("Description", ""))

            score = 0
            for term in terms:
                score += _NAME_WEIGHT * name.count(term)
                score += _TAG_WEIGHT * tags.count(term)
                score += description.count(term)
            if score:
                # index keeps the sort stable for equal scores
                scored.append((score, -index, product))

        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [product for _, _, product in scored[:limit]]

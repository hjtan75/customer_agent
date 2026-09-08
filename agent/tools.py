"""Tool registry: JSON schemas the model sees + the local functions they map to.

Adding a capability = add a method + its schema + one registry entry. Nothing
else in the loop changes. This is the extensibility seam the interview cares
about.
"""

from __future__ import annotations

import json
import secrets

from .promo import DISCOUNT_PERCENT, generate_code, is_promo_active
from .stores import OrderStore, ProductCatalog

TRACKING_URL = "https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking}"

# SKU is an internal identifier and means nothing to a customer. We never send
# it to the model, so it can't recite it back — cheaper and more reliable than
# a prompt rule telling the model not to mention it.
CUSTOMER_FACING_FIELDS = ("ProductName", "Description", "Tags", "Inventory")

# Shown in place of a product whose SKU isn't in the catalog, so the model never
# sees (and so can't recite) the raw identifier. Its presence is what prompts the
# agent to offer a human handoff (see _get_order_status).
UNRESOLVED_ITEM = "an item we can't identify in our catalog"

# Statuses that imply the parcel has left the warehouse, so a missing tracking
# number is an anomaly worth handing to a human.
_SHIPPED_STATUSES = frozenset({"delivered", "in-transit", "shipped", "out-for-delivery"})


def _public_product(product: dict) -> dict:
    """Project a catalog record down to what a customer should ever see."""
    return {k: v for k, v in product.items() if k in CUSTOMER_FACING_FIELDS}


# Tools that act on a specific customer's account. When the registry has an
# authenticated identity, dispatch STAMPS that email over whatever the model
# supplied, so the model can never act on another customer's orders. See
# IMPROVEMENT.md §2.2.
_IDENTITY_BOUND = frozenset({"get_order_status", "list_my_orders"})


class ToolRegistry:
    def __init__(
        self,
        orders: OrderStore,
        catalog: ProductCatalog,
        identity: str | None = None,
    ):
        self._orders = orders
        self._catalog = catalog
        # The authenticated customer email, or None for an anonymous session
        # (the CLI). When set, order tools are bound to this identity.
        self.identity = identity
        self._handlers = {
            "get_order_status": self._get_order_status,
            "recommend_products": self._recommend_products,
            "generate_early_risers_code": self._generate_early_risers_code,
            "list_my_orders": self._list_my_orders,
            "request_handoff": self._request_handoff,
        }

    # -- schemas advertised to the model -------------------------------------

    @property
    def schemas(self) -> list[dict]:
        authed = self.identity is not None

        # When signed in, the model neither sees nor supplies the email — the
        # server already knows who the customer is. When anonymous (CLI), the
        # model must gather both email and order number from the conversation.
        order_props: dict = {
            "order_number": {"type": "string", "description": "e.g. '#W001' or 'W001'"}
        }
        order_required = ["order_number"]
        order_desc = (
            "Look up the signed-in customer's order status and tracking link. "
            "Only the order number is needed; their identity is already known."
        )
        if not authed:
            order_props["email"] = {"type": "string"}
            order_required = ["email", "order_number"]
            order_desc = (
                "Look up a customer's order status and tracking link. "
                "Requires both the customer's email and order number."
            )

        schemas: list[dict] = [
            {
                "type": "function",
                "function": {
                    "name": "get_order_status",
                    "description": order_desc,
                    "parameters": {
                        "type": "object",
                        "properties": order_props,
                        "required": order_required,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "recommend_products",
                    "description": (
                        "Search the Summit Outfitters catalogue for products "
                        "matching a customer's need or query."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "What the customer is looking for.",
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_early_risers_code",
                    "description": (
                        "Check Early Risers eligibility and, if eligible, issue a "
                        "10% discount code. This tool reads the current Pacific "
                        "time itself and enforces the 8-10 AM window — call it to "
                        "find out whether the customer qualifies right now. Only "
                        "call when the customer explicitly requests the promotion. "
                        "Never ask the customer what time it is; this tool knows."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "request_handoff",
                    "description": (
                        "Hand the conversation to a human support teammate. Call "
                        "this when the customer EXPLICITLY asks for a person, or "
                        "when the customer accepts an offer to be connected after "
                        "you hit something you can't resolve. Do NOT call it "
                        "speculatively: for a wall you've hit, first offer a "
                        "handoff and wait for the customer to say yes."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "description": "Short summary of why a human is needed.",
                            },
                            "category": {
                                "type": "string",
                                "enum": [
                                    "customer_request",
                                    "out_of_scope",
                                    "unresolved_item",
                                    "order_error",
                                    "not_found",
                                    "other",
                                ],
                            },
                        },
                        "required": ["reason"],
                    },
                },
            },
        ]

        # Listing a customer's own orders only makes sense once we know who they
        # are, so this tool exists only for an authenticated session.
        if authed:
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": "list_my_orders",
                        "description": (
                            "List all order numbers and their status for the "
                            "signed-in customer. Use when they ask what orders "
                            "they have. No arguments — their identity is known."
                        ),
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            )
        return schemas

    # -- dispatch ------------------------------------------------------------

    def run(self, name: str, arguments: str) -> str:
        """Execute a tool call. Returns a JSON string for the model."""
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        # Stamp the authenticated email over anything the model supplied. This is
        # the authorization boundary: even if the model invents an email arg, a
        # signed-in session can only ever act on its own account.
        if self.identity is not None and name in _IDENTITY_BOUND:
            args["email"] = self.identity
        return json.dumps(handler(**args))

    # -- handlers ------------------------------------------------------------

    def _get_order_status(self, email: str = "", order_number: str = "") -> dict:
        order = self._orders.find(email, order_number)
        if order is None:
            return {"found": False}

        tracking_number = order.get("TrackingNumber")
        status = order.get("Status") or ""
        skus = order.get("ProductsOrdered") or []

        # Resolve SKUs to names, but never expose a raw SKU: an unmatched one
        # becomes a placeholder. Its presence is also a handoff trigger below.
        products = [
            self._catalog.name_for_sku(sku) if self._catalog.has_sku(sku) else UNRESOLVED_ITEM
            for sku in skus
        ]
        has_unresolved = any(not self._catalog.has_sku(sku) for sku in skus)

        result = {
            "found": True,
            "order_number": order.get("OrderNumber"),
            "status": status,
            "products_ordered": products,
        }
        if tracking_number:
            result["tracking_number"] = tracking_number
            result["tracking_link"] = TRACKING_URL.format(tracking=tracking_number)

        # Structural handoff triggers: conditions the code can detect for certain.
        # We surface a reason for the model to OFFER a human — it does the asking
        # and only calls request_handoff if the customer accepts.
        offer = None
        if status.lower() == "error":
            offer = "This order is flagged with an error and needs a teammate to resolve it."
        elif has_unresolved:
            offer = "I can't identify one or more items on this order in our catalog."
        elif status.lower() in _SHIPPED_STATUSES and not tracking_number:
            offer = "This order looks shipped but has no tracking information yet."
        if offer:
            result["handoff_offer"] = offer
        return result

    def _list_my_orders(self, email: str = "") -> dict:
        orders = self._orders.orders_for(email)
        return {
            "orders": [
                {"order_number": o.get("OrderNumber"), "status": o.get("Status")}
                for o in orders
            ]
        }

    def _request_handoff(self, reason: str = "", category: str = "other") -> dict:
        """Escalate to a human. A real system would enqueue the transcript and the
        tool-call trace (both already in the message history) to a support queue;
        here we mint a ticket id and confirm."""
        result = {
            "handed_off": True,
            "ticket": "SUP-" + secrets.token_hex(3).upper(),
            "category": category,
            "reason": reason,
        }
        if self.identity:
            result["customer"] = self.identity
        return result

    def _recommend_products(self, query: str = "") -> dict:
        matches = self._catalog.search(query)
        return {"query": query, "results": [_public_product(p) for p in matches]}

    def _generate_early_risers_code(self) -> dict:
        if not is_promo_active():
            return {
                "eligible": False,
                "reason": "The Early Risers promotion runs 8:00-10:00 AM Pacific.",
            }
        return {
            "eligible": True,
            "code": generate_code(),
            "discount_percent": DISCOUNT_PERCENT,
        }

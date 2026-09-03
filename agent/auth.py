"""Stub authentication shared by the CLI and the web front end.

Deliberately fake: the password is a constant and any email present in the order
data is a valid username. What's real is that identity, once established, is
bound to the session and stamped into order tools server-side (see
ToolRegistry.identity). Swapping this stub for real auth — hashed credentials,
OTP, a session token from the surrounding app — never touches that authorization
boundary. See IMPROVEMENT.md §2.2.
"""

from __future__ import annotations

from .stores import OrderStore

# The one demo credential. Real auth would verify a hashed password against a
# user record; here every known customer shares this.
STUB_PASSWORD = "1234"


def check_login(orders: OrderStore, email: str, password: str) -> bool:
    """True when the email belongs to a known customer and the password matches."""
    return password == STUB_PASSWORD and orders.is_known_email(email)

"""
Request Context — stores tenant information for the current async execution context.
Used for automatic database scoping and logging.
"""
from contextvars import ContextVar

from typing import Optional

# Global context for the current building ID (tenant)
building_id_ctx: ContextVar[Optional[str]] = ContextVar("building_id", default=None)

# Global context for the current user ID
user_id_ctx: ContextVar[Optional[str]] = ContextVar("user_id", default=None)


def get_ctx_building_id() -> Optional[str]:
    """Return the current building_id from context."""
    return building_id_ctx.get()


def set_ctx_building_id(bid: Optional[str]) -> None:
    """Set the current building_id in context."""
    building_id_ctx.set(bid)


def get_ctx_user_id() -> Optional[str]:
    """Return the current user_id from context."""
    return user_id_ctx.get()


def set_ctx_user_id(uid: Optional[str]) -> None:
    """Set the current user_id in context."""
    user_id_ctx.set(uid)

"""Account and corp operations."""

from typing import Any


def show_account(client) -> Any:
    """Get current user account info."""
    return client.get("/v2.1/accounts/show")


def show_corp(client) -> Any:
    """Get corp (company) info."""
    return client.get("/v2.1/corps/show")


def list_entrance(client) -> Any:
    """Get available corp entrances (companies)."""
    return client.post_form("/v2.1/accounts/entrance")

"""Calendar and meal schedule operations."""

from datetime import date as _date
from typing import Any


def list_calendar(client, date: str = None) -> Any:
    """Get calendar items for a date. Returns dateList with available meal tabs."""
    d = date or _date.today().isoformat()
    return client.get("/v2.1/calendarItems/list", {
        "beginDate": d, "endDate": d, "withOrderDetail": "false",
    })


def list_calendar_all(client, date: str = None) -> Any:
    """Get all calendar items for a date (including ordered ones)."""
    d = date or _date.today().isoformat()
    return client.get("/v2.1/calendarItems/all", {
        "beginDate": d, "endDate": d,
    })


def check_status(client) -> Any:
    """Check calendar item ordering status."""
    return client.get("/v2.1/calendarItems/checkStatus")

"""Restaurant and menu operations."""

from typing import Any


def list_restaurants(client, tab_unique_id: str, target_time: str) -> Any:
    """List available restaurants for a meal tab.

    tab_unique_id: from calendarItems/list → userTab.uniqueId
    target_time: e.g. "2026-03-17 09:30"
    """
    return client.get("/v2.1/restaurants/list", {
        "tabUniqueId": tab_unique_id,
        "targetTime": target_time,
    })


def show_restaurant(client, tab_unique_id: str, target_time: str,
                     restaurant_unique_id: str) -> Any:
    """Get restaurant details and full dish list.

    restaurant_unique_id: from restaurants/list → restaurantList[].uniqueId
    """
    return client.get("/v2.1/restaurants/show", {
        "tabUniqueId": tab_unique_id,
        "targetTime": target_time,
        "restaurantUniqueId": restaurant_unique_id,
    })


def list_recommendations(client, tab_unique_id: str, target_time: str) -> Any:
    """Get recommended dishes."""
    return client.get("/v2.1/recommendations/dishes", {
        "tabUniqueId": tab_unique_id,
        "targetTime": target_time,
    })


def list_favourites(client) -> Any:
    """Get user's favourite dishes."""
    return client.get("/v2.1/favourite/all")

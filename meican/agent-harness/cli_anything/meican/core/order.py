"""Order operations: place, pay, cancel, query."""

import json
from typing import Any


def add_order(client, tab_unique_id: str, target_time: str,
              dish_id: int, count: int = 1,
              address_unique_id: str = "", remark: str = "") -> Any:
    """Place an order.

    tab_unique_id: from calendarItems → userTab.uniqueId
    target_time: e.g. "2026-03-17 09:30"
    dish_id: revisionId from restaurants/show → dishList[].id
    address_unique_id: from corpaddresses/getmulticorpaddress
    """
    order_json = json.dumps([{"count": count, "dishId": dish_id}])
    remarks_json = json.dumps([{"dishId": str(dish_id), "remark": remark}])
    return client.post_form("/v2.1/orders/add", {
        "tabUniqueId": tab_unique_id,
        "order": order_json,
        "remarks": remarks_json,
        "targetTime": target_time,
        "userAddressUniqueId": address_unique_id,
        "corpAddressUniqueId": address_unique_id,
        "corpAddressRemark": "",
    })


def pay_order(client, order_response: dict) -> Any:
    """Pay for an order using data from orders/add response.

    The payment API requires signature fields from the order response:
    paymentSlipId, signature, timestamp, mchId, nonceStr
    """
    order = order_response.get("order", {})
    payment_slip_id = order.get("paymentSlipId")
    if not payment_slip_id:
        raise ValueError("No paymentSlipId in order response")
    return client.post_pay(payment_slip_id, order_response)


def delete_order(client, unique_id: str, order_type: str = "CORP_ORDER",
                 restore_cart: bool = False) -> Any:
    """Cancel/delete an order.

    unique_id: order uniqueId from orders/add response or order detail
    """
    return client.post_form("/v2.1/orders/delete", {
        "uniqueId": unique_id,
        "type": order_type,
        "restoreCart": str(restore_cart).lower(),
    })


def get_order(client, unique_id: str) -> Any:
    """Get order details."""
    return client.get(f"/gateway/group-meals/v1/order/{unique_id}")


def list_unpaid(client) -> Any:
    """List unpaid orders."""
    return client.get("/v2.1/orders/unpaidList")


def get_addresses(client, namespace: str = "") -> Any:
    """Get delivery addresses. Namespace is the corp namespace (e.g. '419239')."""
    params = {}
    if namespace:
        params["namespace"] = namespace
    else:
        # Auto-detect from account info
        try:
            acct = client.get("/v2.1/accounts/show")
            corps = acct.get("corpList", [])
            if corps:
                params["namespace"] = corps[0].get("namespace", "")
        except Exception:
            pass
    return client.get("/v2.1/corpaddresses/getmulticorpaddress", params)


def query_cart(client, tab_uuid: str, close_time: str) -> Any:
    """Query current cart contents."""
    return client.post_form("/preorder/cart/query", {
        "tabUUID": tab_uuid,
        "closeTime": close_time,
    })


def update_cart(client, cart_data: dict) -> Any:
    """Update cart (add/remove dishes). cart_data is the full cart JSON body."""
    return client.post("/preorder/cart/update", cart_data)

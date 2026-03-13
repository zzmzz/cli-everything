"""CLI entry point for meican (美餐)."""

import sys

import click

from .utils.http_client import HttpClient, APIError
from .utils.output import output_result, output_success, output_error


def _client(ctx) -> HttpClient:
    return ctx.obj["client"]


@click.group()
@click.option("--base-url", envvar="MEICAN_BASE_URL", default=None, help="API base URL")
@click.option("--token", envvar="MEICAN_TOKEN", default=None, help="Access token")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def cli(ctx, base_url, token, as_json):
    """meican (美餐) — corporate meal ordering CLI."""
    ctx.ensure_object(dict)
    ctx.obj["client"] = HttpClient(base_url=base_url, token=token)
    ctx.obj["json"] = as_json


# ---- Auth ----

@cli.command()
@click.option("--email", prompt=True, help="Login email")
@click.option("--password", prompt=True, hide_input=True, help="Login password")
@click.pass_context
def login(ctx, email, password):
    """Login with email and password (GraphQL two-step auth)."""
    try:
        result = _client(ctx).login(email, password)
        if ctx.obj["json"]:
            output_result(result, True)
        else:
            output_success(f"Logged in as {result.get('user', email)}")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


@cli.command()
@click.pass_context
def refresh(ctx):
    """Refresh access token."""
    try:
        result = _client(ctx).refresh()
        if ctx.obj["json"]:
            output_result(result, True)
        else:
            output_success("Token refreshed")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


# ---- Account ----

@cli.command("whoami")
@click.pass_context
def whoami(ctx):
    """Show current user info."""
    from .core.account import show_account
    try:
        data = show_account(_client(ctx))
        if ctx.obj["json"]:
            output_result(data, True)
        else:
            print(f"  User:   {data.get('username', '?')}")
            print(f"  Email:  {data.get('email', '?')}")
            print(f"  ID:     {data.get('uniqueId', '?')}")
            corps = data.get("corpList", [])
            if corps:
                print(f"  Corp:   {corps[0].get('name', '?')} ({corps[0].get('namespace', '')})")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


# ---- Calendar ----

@cli.group()
def calendar():
    """View meal calendar and schedules."""
    pass


@calendar.command("list")
@click.option("--date", default=None, help="Date (YYYY-MM-DD), default today")
@click.pass_context
def calendar_list(ctx, date):
    """List available meal tabs for a date."""
    from .core.calendar import list_calendar
    try:
        data = list_calendar(_client(ctx), date)
        if ctx.obj["json"]:
            output_result(data, True)
        else:
            for date_item in data.get("dateList", []):
                print(f"\n  Date: {date_item['date']}")
                for item in date_item.get("calendarItemList", []):
                    tab = item.get("userTab", {})
                    status = item.get("corpOrderUser") or {}
                    ordered = "ORDERED" if status.get("restaurantItemList") else "OPEN"
                    title = item.get("title", "?")
                    tab_id = tab.get("uniqueId", "?")
                    target = item.get("targetTime", "")
                    print(f"    [{ordered:7s}] {title}")
                    print(f"             tab={tab_id}  targetTime={target}")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


# ---- Restaurant ----

@cli.group()
def restaurant():
    """Browse restaurants and menus."""
    pass


@restaurant.command("list")
@click.option("--tab-id", required=True, help="Tab unique ID (from calendar list)")
@click.option("--time", "target_time", required=True, help="Target time (e.g. '2026-03-17 09:30')")
@click.pass_context
def restaurant_list(ctx, tab_id, target_time):
    """List available restaurants for a meal tab."""
    from .core.restaurant import list_restaurants
    try:
        data = list_restaurants(_client(ctx), tab_id, target_time)
        if ctx.obj["json"]:
            output_result(data, True)
        else:
            for r in data.get("restaurantList", []):
                status = "OPEN" if r.get("open") else "CLOSED"
                print(f"  [{status:6s}] {r['name']}")
                print(f"           id={r['uniqueId']}  dishes={r.get('availableDishCount', '?')}")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


@restaurant.command("menu")
@click.option("--tab-id", required=True, help="Tab unique ID")
@click.option("--time", "target_time", required=True, help="Target time")
@click.option("--restaurant-id", required=True, help="Restaurant unique ID")
@click.pass_context
def restaurant_menu(ctx, tab_id, target_time, restaurant_id):
    """Show restaurant menu (dish list)."""
    from .core.restaurant import show_restaurant
    try:
        data = show_restaurant(_client(ctx), tab_id, target_time, restaurant_id)
        if ctx.obj["json"]:
            output_result(data, True)
        else:
            print(f"  Restaurant: {data.get('name', '?')}")
            print(f"  Dishes:")
            for dish in data.get("dishList", []):
                price = dish.get("priceInCent", 0) / 100
                print(f"    [{dish.get('id', '?'):>10}] ¥{price:.0f}  {dish['name'][:60]}")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


# ---- Order ----

@cli.group()
def order():
    """Place, view, and cancel orders."""
    pass


@order.command("place")
@click.option("--tab-id", required=True, help="Tab unique ID")
@click.option("--time", "target_time", required=True, help="Target time")
@click.option("--dish-id", required=True, type=int, help="Dish revision ID")
@click.option("--address-id", default="", help="Delivery address unique ID")
@click.option("--count", default=1, type=int, help="Quantity")
@click.option("--pay/--no-pay", default=True, help="Auto-pay after placing order")
@click.pass_context
def order_place(ctx, tab_id, target_time, dish_id, address_id, count, pay):
    """Place an order and optionally auto-pay."""
    from .core.order import add_order, pay_order
    client = _client(ctx)

    # If no address, try to get the first one
    if not address_id:
        from .core.order import get_addresses
        try:
            addr_data = get_addresses(client)
            addr_list = addr_data.get("addressList", [])
            if addr_list:
                address_id = addr_list[0].get("finalValue", {}).get("uniqueId", "")
        except Exception:
            pass

    try:
        result = add_order(client, tab_id, target_time, dish_id, count, address_id)
        order_id = result.get("order", {}).get("uniqueId", "?")

        if pay and result.get("status") == "SUCCESSFUL":
            try:
                pay_result = pay_order(client, result)
                if ctx.obj["json"]:
                    output_result({"order": result, "payment": pay_result}, True)
                else:
                    output_success(f"Order {order_id} placed and paid")
                return
            except Exception as e:
                if ctx.obj["json"]:
                    output_result(result, True)
                else:
                    output_success(f"Order {order_id} placed (payment failed: {e})")
                return

        if ctx.obj["json"]:
            output_result(result, True)
        else:
            output_success(f"Order {order_id} placed (status: {result.get('status', '?')})")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


@order.command("cancel")
@click.argument("unique_id")
@click.pass_context
def order_cancel(ctx, unique_id):
    """Cancel an order by its unique ID."""
    from .core.order import delete_order
    try:
        result = delete_order(_client(ctx), unique_id)
        if ctx.obj["json"]:
            output_result(result, True)
        else:
            output_success(f"Order {unique_id} cancelled")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


@order.command("show")
@click.argument("unique_id")
@click.pass_context
def order_show(ctx, unique_id):
    """Show order details."""
    from .core.order import get_order
    try:
        data = get_order(_client(ctx), unique_id)
        if ctx.obj["json"]:
            output_result(data, True)
        else:
            o = data.get("order", data)
            print(f"  Order:   {o.get('uniqueId', unique_id)}")
            print(f"  Status:  {o.get('orderStatus', o.get('status', '?'))}")
            print(f"  Title:   {o.get('title', '?')}")
            dishes = o.get("dishes", o.get("dishItemList", []))
            if dishes:
                print(f"  Dishes:")
                for d in dishes:
                    name = d.get("dishName", d.get("name", "?"))
                    print(f"    - {name}")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


@order.command("unpaid")
@click.pass_context
def order_unpaid(ctx):
    """List unpaid orders."""
    from .core.order import list_unpaid
    try:
        data = list_unpaid(_client(ctx))
        if ctx.obj["json"]:
            output_result(data, True)
        else:
            orders = data.get("corpOrderUserList", [])
            if not orders:
                print("  No unpaid orders")
            else:
                for o in orders:
                    print(f"  {o.get('uniqueId', '?')} — {o.get('title', '?')}")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


@order.command("addresses")
@click.pass_context
def order_addresses(ctx):
    """List delivery addresses."""
    from .core.order import get_addresses
    try:
        data = get_addresses(_client(ctx))
        if ctx.obj["json"]:
            output_result(data, True)
        else:
            for a in data.get("addressList", []):
                fv = a.get("finalValue", {})
                print(f"  {fv.get('uniqueId', '?'):20s}  {a.get('name', '?')}")
    except APIError as e:
        output_error(str(e))
        sys.exit(1)


def main():
    cli(obj={})


if __name__ == "__main__":
    main()

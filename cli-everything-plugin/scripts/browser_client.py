#!/usr/bin/env python3
"""Browser client — convenience CLI for interacting with browser_server.py.

Usage:
    python browser_client.py screenshot
    python browser_client.py requests
    python browser_client.py click "button.submit"
    python browser_client.py fill "input[name=username]" "myuser"
    python browser_client.py navigate "https://example.com/login"
    python browser_client.py page-info
    python browser_client.py elements
    python browser_client.py export-catalog --site-name mysite
    python browser_client.py close
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

DEFAULT_PORT = 8766
BASE = "http://127.0.0.1:{port}"


def _request(method: str, path: str, data: dict | None = None, port: int = DEFAULT_PORT) -> dict:
    url = BASE.format(port=port) + path
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())
    except urllib.error.URLError as e:
        return {"error": f"Cannot connect to server on port {port}: {e}"}


def main():
    parser = argparse.ArgumentParser(description="Browser server client")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("health")
    sub.add_parser("screenshot")
    sub.add_parser("page-info")
    sub.add_parser("requests")
    sub.add_parser("requests-summary")
    sub.add_parser("requests-clear")
    sub.add_parser("close")

    p = sub.add_parser("navigate")
    p.add_argument("url")

    p = sub.add_parser("click")
    p.add_argument("selector")

    p = sub.add_parser("fill")
    p.add_argument("selector")
    p.add_argument("value")

    p = sub.add_parser("type")
    p.add_argument("selector")
    p.add_argument("text")

    p = sub.add_parser("press")
    p.add_argument("key")
    p.add_argument("--selector", default="")

    p = sub.add_parser("select")
    p.add_argument("selector")
    p.add_argument("value")

    p = sub.add_parser("scroll")
    p.add_argument("--x", type=int, default=0)
    p.add_argument("--y", type=int, default=300)

    p = sub.add_parser("evaluate")
    p.add_argument("expression")

    p = sub.add_parser("wait")
    p.add_argument("selector")
    p.add_argument("--timeout", type=int, default=10000)

    p = sub.add_parser("elements")
    p.add_argument("--selector", default="")

    p = sub.add_parser("export-catalog")
    p.add_argument("--site-name", default="")

    args = parser.parse_args()
    port = args.port

    if not args.cmd:
        parser.print_help()
        return

    if args.cmd == "health":
        r = _request("GET", "/health", port=port)
    elif args.cmd == "screenshot":
        r = _request("GET", "/screenshot", port=port)
    elif args.cmd == "page-info":
        r = _request("GET", "/page-info", port=port)
    elif args.cmd == "requests":
        r = _request("GET", "/requests", port=port)
    elif args.cmd == "requests-summary":
        r = _request("GET", "/requests/summary", port=port)
    elif args.cmd == "requests-clear":
        r = _request("GET", "/requests/clear", port=port)
    elif args.cmd == "close":
        r = _request("POST", "/close", port=port)
    elif args.cmd == "navigate":
        r = _request("POST", "/navigate", {"url": args.url}, port=port)
    elif args.cmd == "click":
        r = _request("POST", "/click", {"selector": args.selector}, port=port)
    elif args.cmd == "fill":
        r = _request("POST", "/fill", {"selector": args.selector, "value": args.value}, port=port)
    elif args.cmd == "type":
        r = _request("POST", "/type", {"selector": args.selector, "text": args.text}, port=port)
    elif args.cmd == "press":
        r = _request("POST", "/press", {"key": args.key, "selector": args.selector}, port=port)
    elif args.cmd == "select":
        r = _request("POST", "/select", {"selector": args.selector, "value": args.value}, port=port)
    elif args.cmd == "scroll":
        r = _request("POST", "/scroll", {"x": args.x, "y": args.y}, port=port)
    elif args.cmd == "evaluate":
        r = _request("POST", "/evaluate", {"expression": args.expression}, port=port)
    elif args.cmd == "wait":
        r = _request("POST", "/wait", {"selector": args.selector, "timeout": args.timeout}, port=port)
    elif args.cmd == "elements":
        data = {}
        if args.selector:
            data["selector"] = args.selector
        r = _request("GET", f"/elements{'?selector=' + args.selector if args.selector else ''}", port=port)
    elif args.cmd == "export-catalog":
        r = _request("POST", "/export-catalog", {"site_name": args.site_name}, port=port)
    else:
        print(f"Unknown command: {args.cmd}", file=sys.stderr)
        return

    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Chrome CDP Recorder — passive observation of user-driven browser sessions.

Launches Chrome with remote debugging enabled, connects via CDP, and passively
records all network requests, user interactions, and page JS. The user operates
the browser manually; this script just watches and records everything.

Exposes an HTTP API for the agent to query recorded data and trigger analysis.

Usage:
    python chrome_recorder.py --url <url> [--port 8766] [--state-dir /tmp/cli-everything]
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

import websocket  # pip install websocket-client

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {
    "chrome_pid": None,
    "ws_url": None,
    "connected": False,
    "captured_requests": [],       # completed request/response pairs
    "pending_requests": {},        # requestId -> partial entry
    "user_actions": [],            # user interaction events
    "page_navigations": [],        # navigation events
    "script_sources": {},          # scriptId -> {url, source_snippet}
    "console_messages": [],        # console.log etc
    "state_dir": "/tmp/cli-everything",
    "running": True,
    "ws": None,
    "msg_id": 0,
    "pending_responses": {},       # msg_id -> threading.Event + result
}
_lock = threading.Lock()

STATIC_EXTENSIONS = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".webp", ".avif",
    ".mp4", ".mp3", ".webm", ".ogg",
}

STATIC_PATH_PATTERNS = re.compile(
    r"(/static/|/assets/|/media/|/lottie/|/dist/|/build/|/public/|/images/|/fonts/)",
    re.IGNORECASE,
)


def _is_static(url: str) -> bool:
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in STATIC_EXTENSIONS):
        return True
    if STATIC_PATH_PATTERNS.search(path_lower):
        return True
    return False


# ---------------------------------------------------------------------------
# Chrome launcher
# ---------------------------------------------------------------------------
def _find_chrome() -> str:
    """Find Chrome executable on the system."""
    candidates = [
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        # Linux
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    import shutil
    for c in candidates:
        if os.path.isfile(c):
            return c
        found = shutil.which(c)
        if found:
            return found
    raise RuntimeError("Cannot find Chrome. Install Google Chrome or set CHROME_PATH env var.")


def _launch_chrome(url: str, debug_port: int = 9222, user_data_dir: str | None = None) -> subprocess.Popen:
    """Launch Chrome with remote debugging port."""
    chrome_path = os.environ.get("CHROME_PATH") or _find_chrome()

    if not user_data_dir:
        user_data_dir = tempfile.mkdtemp(prefix="chrome-recorder-")

    args = [
        chrome_path,
        f"--remote-debugging-port={debug_port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,900",
        url,
    ]

    print(f"[chrome] Launching: {chrome_path}", file=sys.stderr)
    print(f"[chrome] Debug port: {debug_port}", file=sys.stderr)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _state["chrome_pid"] = proc.pid
    return proc


def _get_ws_url(debug_port: int = 9222, max_wait: int = 30) -> str:
    """Get the WebSocket debugger URL from Chrome's /json endpoint."""
    import urllib.request
    import urllib.error

    for _ in range(max_wait * 2):
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json")
            tabs = json.loads(resp.read())
            for tab in tabs:
                if tab.get("type") == "page" and "webSocketDebuggerUrl" in tab:
                    return tab["webSocketDebuggerUrl"]
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Chrome did not start on port {debug_port} within {max_wait}s")


# ---------------------------------------------------------------------------
# CDP connection
# ---------------------------------------------------------------------------
def _send_cdp(method: str, params: dict = None, timeout: float = 10) -> dict:
    """Send a CDP command and wait for the response."""
    ws = _state.get("ws")
    if not ws:
        return {"error": "Not connected"}

    with _lock:
        _state["msg_id"] += 1
        msg_id = _state["msg_id"]

    event = threading.Event()
    _state["pending_responses"][msg_id] = {"event": event, "result": None}

    msg = {"id": msg_id, "method": method}
    if params:
        msg["params"] = params

    try:
        ws.send(json.dumps(msg))
    except Exception as e:
        return {"error": f"WebSocket send failed: {e}"}

    if event.wait(timeout=timeout):
        result = _state["pending_responses"].pop(msg_id, {}).get("result", {})
        return result
    else:
        _state["pending_responses"].pop(msg_id, None)
        return {"error": f"CDP command '{method}' timed out"}


def _on_cdp_message(ws, raw_msg):
    """Handle incoming CDP messages (events and command responses)."""
    try:
        msg = json.loads(raw_msg)
    except json.JSONDecodeError:
        return

    # Command response
    if "id" in msg:
        msg_id = msg["id"]
        pending = _state["pending_responses"].get(msg_id)
        if pending:
            pending["result"] = msg.get("result", msg.get("error", {}))
            pending["event"].set()
        return

    # Event
    method = msg.get("method", "")
    params = msg.get("params", {})

    try:
        if method == "Network.requestWillBeSent":
            _handle_request_will_be_sent(params)
        elif method == "Network.responseReceived":
            _handle_response_received(params)
        elif method == "Network.loadingFinished":
            _handle_loading_finished(params)
        elif method == "Network.loadingFailed":
            _handle_loading_failed(params)
        elif method == "Page.navigatedWithinDocument" or method == "Page.frameNavigated":
            _handle_navigation(method, params)
        elif method == "Runtime.consoleAPICalled":
            _handle_console(params)
        elif method == "Runtime.bindingCalled":
            _handle_binding_called(params)
    except Exception as e:
        print(f"[cdp] Error handling {method}: {e}", file=sys.stderr)


def _handle_request_will_be_sent(params):
    request = params.get("request", {})
    url = request.get("url", "")
    if _is_static(url):
        return

    req_id = params.get("requestId", "")
    entry = {
        "requestId": req_id,
        "timestamp": time.time(),
        "method": request.get("method", "GET"),
        "url": url,
        "request_headers": request.get("headers", {}),
        "request_body": None,
        "response_status": None,
        "response_headers": {},
        "response_body": None,
        "resource_type": params.get("type", ""),
        "initiator": params.get("initiator", {}),
    }

    # POST data
    if request.get("postData"):
        body = request["postData"]
        try:
            entry["request_body"] = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            entry["request_body"] = body

    with _lock:
        _state["pending_requests"][req_id] = entry


def _handle_response_received(params):
    req_id = params.get("requestId", "")
    response = params.get("response", {})

    with _lock:
        entry = _state["pending_requests"].get(req_id)
        if not entry:
            return
        entry["response_status"] = response.get("status")
        entry["response_headers"] = response.get("headers", {})
        # Also update URL in case of redirect
        if response.get("url"):
            entry["url"] = response["url"]


def _handle_loading_finished(params):
    req_id = params.get("requestId", "")

    with _lock:
        entry = _state["pending_requests"].pop(req_id, None)

    if not entry:
        return

    # Only capture XHR/Fetch (filter out Document, Script, Stylesheet, Image, etc.)
    # CDP uses capitalized type names: XHR, Fetch, Document, Script, etc.
    rtype = entry.get("resource_type", "")
    if rtype not in ("XHR", "Fetch", "Other", ""):
        return

    # Store entry with requestId — response body will be fetched lazily
    # (can't call _send_cdp from on_message callback: same-thread deadlock)
    clean_entry = {
        "timestamp": entry["timestamp"],
        "method": entry["method"],
        "url": entry["url"],
        "resource_type": entry["resource_type"],
        "status": entry["response_status"],
        "request_headers": entry["request_headers"],
        "request_body": entry["request_body"],
        "response_body": None,
        "initiator": entry.get("initiator", {}),
        "_requestId": req_id,  # kept for lazy body fetch
        "_body_fetched": False,
    }

    with _lock:
        _state["captured_requests"].append(clean_entry)
        sd = Path(_state["state_dir"])
        with open(sd / "requests.jsonl", "a", encoding="utf-8") as f:
            log_entry = {k: v for k, v in clean_entry.items()
                         if k not in ("initiator", "_requestId", "_body_fetched")}
            f.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")


def _fetch_response_bodies():
    """Fetch response bodies for captured requests (called from HTTP thread or background thread)."""
    with _lock:
        to_fetch = [(i, r["_requestId"]) for i, r in enumerate(_state["captured_requests"])
                     if not r.get("_body_fetched") and r.get("_requestId")]

    for idx, req_id in to_fetch:
        try:
            result = _send_cdp("Network.getResponseBody", {"requestId": req_id}, timeout=3)
            if "body" in result:
                body = result["body"]
                if result.get("base64Encoded"):
                    parsed_body = "(binary data)"
                else:
                    try:
                        parsed_body = json.loads(body)
                    except (json.JSONDecodeError, TypeError):
                        if len(body) > 5000:
                            parsed_body = body[:5000] + "...(truncated)"
                        else:
                            parsed_body = body
                with _lock:
                    _state["captured_requests"][idx]["response_body"] = parsed_body
        except Exception:
            pass
        with _lock:
            _state["captured_requests"][idx]["_body_fetched"] = True


def _body_fetcher_loop():
    """Background thread that eagerly fetches response bodies before Chrome discards them."""
    while _state["running"]:
        time.sleep(0.5)
        if not _state.get("connected"):
            continue
        try:
            _fetch_response_bodies()
        except Exception as e:
            print(f"[body-fetcher] Error: {e}", file=sys.stderr)


def _handle_loading_failed(params):
    req_id = params.get("requestId", "")
    with _lock:
        _state["pending_requests"].pop(req_id, None)


def _handle_navigation(method, params):
    frame = params.get("frame", {})
    nav = {
        "timestamp": time.time(),
        "event": method,
        "url": frame.get("url") or params.get("url", ""),
    }
    with _lock:
        _state["page_navigations"].append(nav)


def _handle_console(params):
    args = params.get("args", [])
    text_parts = []
    for arg in args[:5]:
        if "value" in arg:
            text_parts.append(str(arg["value"]))
        elif arg.get("description"):
            text_parts.append(arg["description"][:200])
    with _lock:
        _state["console_messages"].append({
            "timestamp": time.time(),
            "type": params.get("type", "log"),
            "text": " ".join(text_parts)[:500],
        })


def _handle_binding_called(params):
    """Handle calls from injected JS observer (user actions)."""
    name = params.get("name", "")
    if name != "__recorderEvent":
        return
    try:
        payload = json.loads(params.get("payload", "{}"))
        with _lock:
            _state["user_actions"].append(payload)
            sd = Path(_state["state_dir"])
            with open(sd / "actions.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except (json.JSONDecodeError, TypeError):
        pass


# ---------------------------------------------------------------------------
# User interaction observer (injected into every page)
# ---------------------------------------------------------------------------
OBSERVER_JS = """
(function() {
    if (window.__recorderInstalled) return;
    window.__recorderInstalled = true;

    function describeElement(el) {
        if (!el || !el.tagName) return null;
        const tag = el.tagName.toLowerCase();
        const info = {tag: tag};
        if (el.id) info.id = el.id;
        if (el.name) info.name = el.name;
        if (el.className && typeof el.className === 'string')
            info.className = el.className.slice(0, 100);
        if (el.type) info.type = el.type;
        if (el.href) info.href = el.href;
        if (el.placeholder) info.placeholder = el.placeholder;
        const text = (el.textContent || '').trim();
        if (text.length > 0 && text.length < 80) info.text = text;
        // Build a CSS selector
        let selector = tag;
        if (el.id) selector = '#' + el.id;
        else if (el.name) selector = tag + '[name="' + el.name + '"]';
        else if (el.className && typeof el.className === 'string') {
            const cls = el.className.trim().split(/\\s+/)[0];
            if (cls) selector = tag + '.' + cls;
        }
        info.selector = selector;
        return info;
    }

    // Click observer
    document.addEventListener('click', function(e) {
        const info = describeElement(e.target);
        if (!info) return;
        try {
            window.__recorderEvent(JSON.stringify({
                type: 'click',
                timestamp: Date.now(),
                element: info,
                x: e.clientX,
                y: e.clientY,
                url: location.href,
            }));
        } catch(err) {}
    }, true);

    // Input change observer
    document.addEventListener('change', function(e) {
        const info = describeElement(e.target);
        if (!info) return;
        try {
            window.__recorderEvent(JSON.stringify({
                type: 'change',
                timestamp: Date.now(),
                element: info,
                value: (e.target.value || '').slice(0, 200),
                url: location.href,
            }));
        } catch(err) {}
    }, true);

    // Form submit observer
    document.addEventListener('submit', function(e) {
        const form = e.target;
        const inputs = Array.from(form.querySelectorAll('input, select, textarea'));
        const fields = inputs.map(function(inp) {
            return {
                name: inp.name || inp.id || inp.type,
                type: inp.type,
                value: inp.type === 'password' ? '***' : (inp.value || '').slice(0, 200),
            };
        });
        try {
            window.__recorderEvent(JSON.stringify({
                type: 'submit',
                timestamp: Date.now(),
                action: form.action,
                method: form.method,
                fields: fields,
                url: location.href,
            }));
        } catch(err) {}
    }, true);

    // Keyboard (Enter key in inputs)
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && e.target.tagName &&
            ['INPUT', 'TEXTAREA'].includes(e.target.tagName.toUpperCase())) {
            const info = describeElement(e.target);
            try {
                window.__recorderEvent(JSON.stringify({
                    type: 'keydown',
                    timestamp: Date.now(),
                    key: e.key,
                    element: info,
                    url: location.href,
                }));
            } catch(err) {}
        }
    }, true);

    // Focus tracking for input fields
    document.addEventListener('focus', function(e) {
        if (e.target.tagName &&
            ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName.toUpperCase())) {
            const info = describeElement(e.target);
            try {
                window.__recorderEvent(JSON.stringify({
                    type: 'focus',
                    timestamp: Date.now(),
                    element: info,
                    url: location.href,
                }));
            } catch(err) {}
        }
    }, true);
})();
"""


def _inject_observer():
    """Inject the user interaction observer into the current page."""
    # Register binding first (to receive events from page JS)
    _send_cdp("Runtime.addBinding", {"name": "__recorderEvent"})
    # Inject observer script
    _send_cdp("Runtime.evaluate", {"expression": OBSERVER_JS})
    # Also set it to auto-inject on new pages/navigations
    _send_cdp("Page.addScriptToEvaluateOnNewDocument", {"source": OBSERVER_JS})


# ---------------------------------------------------------------------------
# CDP thread — maintains the WebSocket connection
# ---------------------------------------------------------------------------
def _cdp_send_fire_and_forget(ws, method: str, params: dict = None):
    """Send a CDP command without waiting for response (used during init)."""
    with _lock:
        _state["msg_id"] += 1
        msg_id = _state["msg_id"]
    msg = {"id": msg_id, "method": method}
    if params:
        msg["params"] = params
    ws.send(json.dumps(msg))


def _run_cdp(ws_url: str, state_dir: str):
    """Connect to Chrome via CDP and listen for events. Auto-reconnects on disconnect."""
    debug_port = _state.get("debug_port", 9222)

    while _state["running"]:
        # Resolve the current ws_url (tab may have changed)
        try:
            current_ws_url = _get_ws_url(debug_port, max_wait=5)
        except RuntimeError:
            current_ws_url = ws_url

        def on_open(ws):
            print(f"[cdp] Connected to {current_ws_url}", file=sys.stderr)
            _state["connected"] = True
            _state["ws"] = ws

            # Enable CDP domains (fire-and-forget to avoid deadlock —
            # on_open and on_message share the same thread)
            _cdp_send_fire_and_forget(ws, "Network.enable", {
                "maxResourceBufferSize": 10 * 1024 * 1024,
                "maxTotalBufferSize": 50 * 1024 * 1024,
            })
            _cdp_send_fire_and_forget(ws, "Page.enable")
            _cdp_send_fire_and_forget(ws, "Runtime.enable")
            _cdp_send_fire_and_forget(ws, "Runtime.addBinding", {"name": "__recorderEvent"})
            _cdp_send_fire_and_forget(ws, "Runtime.evaluate", {"expression": OBSERVER_JS})
            _cdp_send_fire_and_forget(ws, "Page.addScriptToEvaluateOnNewDocument", {"source": OBSERVER_JS})

            print("[cdp] Recording started. User can now operate the browser.", file=sys.stderr)

        def on_close(ws, close_status_code, close_msg):
            print(f"[cdp] WebSocket closed (will reconnect...)", file=sys.stderr)
            _state["connected"] = False
            _state["ws"] = None

        def on_error(ws, error):
            print(f"[cdp] WebSocket error: {error}", file=sys.stderr)

        ws = websocket.WebSocketApp(
            current_ws_url,
            on_open=on_open,
            on_message=_on_cdp_message,
            on_close=on_close,
            on_error=on_error,
        )

        ws.run_forever()

        if not _state["running"]:
            break

        # Auto-reconnect after a short delay
        print("[cdp] Reconnecting in 2s...", file=sys.stderr)
        time.sleep(2)


# ---------------------------------------------------------------------------
# Analysis helpers (reused from browser_server.py)
# ---------------------------------------------------------------------------
def _extract_values_flat(obj, prefix="") -> dict[str, str]:
    result = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            _p = f"{prefix}.{k}" if prefix else k
            result.update(_extract_values_flat(v, _p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            result.update(_extract_values_flat(v, f"{prefix}[{i}]"))
    elif obj is not None:
        s = str(obj)
        if len(s) >= 4:
            result[prefix or "value"] = s
    return result


def _trace_param_sources(requests: list[dict]) -> dict:
    traced: list[dict] = []
    known_values: dict[str, list[dict]] = {}

    for i, req in enumerate(requests):
        req_params = {}
        if req.get("request_body"):
            req_params.update(_extract_values_flat(req["request_body"], "body"))
        parsed = urlparse(req["url"])
        if parsed.query:
            for k, vs in parse_qs(parsed.query).items():
                for v in vs:
                    if len(v) >= 4:
                        req_params[f"query.{k}"] = v

        ignore_headers = {
            "accept", "content-type", "user-agent", "origin", "referer",
            "accept-language", "accept-encoding", "connection", "host",
            "content-length", "pragma", "cache-control", "sec-ch-ua",
            "sec-ch-ua-mobile", "sec-ch-ua-platform", "sec-fetch-dest",
            "sec-fetch-mode", "sec-fetch-site",
        }
        for hdr, val in req.get("request_headers", {}).items():
            if hdr.lower() not in ignore_headers and len(str(val)) >= 6:
                req_params[f"header.{hdr}"] = str(val)

        dependencies = []
        for param_path, param_val in req_params.items():
            if param_val in known_values:
                for src in known_values[param_val][:3]:
                    dependencies.append({
                        "param": param_path,
                        "value": param_val[:80],
                        "source": f"request[{src['index']}] ({requests[src['index']]['method']} {urlparse(requests[src['index']]['url']).path})",
                        "source_path": src["path"],
                    })

        traced.append({
            "index": i,
            "method": req["method"],
            "url_path": urlparse(req["url"]).path,
            "dependencies": dependencies,
        })

        if req.get("response_body"):
            resp_vals = _extract_values_flat(req["response_body"], "response")
            for path, val in resp_vals.items():
                if val not in known_values:
                    known_values[val] = []
                known_values[val].append({"index": i, "path": path})

        if parsed.query:
            for k, vs in parse_qs(parsed.query).items():
                for v in vs:
                    if len(v) >= 4:
                        if v not in known_values:
                            known_values[v] = []
                        known_values[v].append({"index": i, "path": f"url.query.{k}"})

    return {
        "total_requests": len(requests),
        "traced": [t for t in traced if t["dependencies"]],
        "all_traced": traced,
    }


def _build_catalog(requests: list[dict], site_name: str = "") -> dict:
    if not requests:
        return {"site_name": site_name or "unknown", "base_url": "", "domains": []}

    urls = [r["url"] for r in requests]
    if not site_name:
        hostnames = [urlparse(u).netloc for u in urls]
        most_common = Counter(hostnames).most_common(1)[0][0]
        parts = most_common.split(".")
        site_name = parts[-2] if len(parts) >= 2 else parts[0]

    parsed_urls = [urlparse(u) for u in urls]
    netloc_counter = Counter(f"{p.scheme}://{p.netloc}" for p in parsed_urls)
    base_url = netloc_counter.most_common(1)[0][0]

    auth_info = _detect_auth(requests)

    json_responses = [r["response_body"] for r in requests if isinstance(r.get("response_body"), dict)]
    envelope = _detect_envelope(json_responses)

    _dynamic_seg = re.compile(r"^([0-9a-f]{8,}|[0-9a-f]{8}-[0-9a-f]{4}-|\d{6,})$", re.IGNORECASE)
    seen: dict[str, dict] = {}
    for r in requests:
        parsed = urlparse(r["url"])
        parts = parsed.path.strip("/").split("/")
        normalized = ["{id}" if _dynamic_seg.match(p) else p for p in parts]
        norm_path = "/" + "/".join(normalized)
        key = f"{r['method']}:{norm_path}"
        if key not in seen:
            seen[key] = {
                "method": r["method"],
                "path": norm_path,
                "request_example": r.get("request_body"),
                "response_example": r.get("response_body"),
                "status": r.get("status"),
            }

    base_path = urlparse(base_url).path.rstrip("/")
    domain_groups: dict[str, list] = defaultdict(list)
    for ep in seen.values():
        rel = ep["path"]
        if base_path and rel.startswith(base_path):
            rel = rel[len(base_path):]
        parts = [p for p in rel.strip("/").split("/") if p]
        domain_name = parts[0] if parts else "root"
        domain_groups[domain_name].append(ep)

    domains = []
    for name, eps in sorted(domain_groups.items()):
        paths = [ep["path"] for ep in eps]
        prefix_parts = paths[0].strip("/").split("/") if paths else []
        common = []
        for i, part in enumerate(prefix_parts):
            if all(
                p.strip("/").split("/")[i] == part
                for p in paths
                if len(p.strip("/").split("/")) > i
            ):
                common.append(part)
            else:
                break
        prefix = "/" + "/".join(common) if common else f"/{name}"
        domains.append({"name": name, "prefix": prefix, "endpoints": eps})

    env_prefix = site_name.upper().replace("-", "_")
    return {
        "site_name": site_name,
        "base_url": base_url,
        "auth_type": auth_info.get("auth_type", "unknown"),
        "auth_details": {**auth_info, "env_var": f"{env_prefix}_TOKEN"},
        "response_envelope": envelope,
        "domains": domains,
    }


def _detect_auth(requests: list[dict]) -> dict:
    cookie_count = auth_header_count = 0
    custom_headers: dict[str, int] = {}
    ignore = {
        "accept", "content-type", "user-agent", "origin", "referer",
        "accept-language", "accept-encoding", "connection", "host",
        "content-length", "pragma", "cache-control",
    }
    auth_pat = re.compile(r"(token|secret|signature|session|auth)", re.IGNORECASE)
    for r in requests:
        hdrs = r.get("request_headers", {})
        if "authorization" in {k.lower() for k in hdrs}:
            auth_header_count += 1
        if "cookie" in {k.lower() for k in hdrs}:
            cookie_count += 1
        for name in hdrs:
            if name.lower() in ignore:
                continue
            if auth_pat.search(name):
                custom_headers[name] = custom_headers.get(name, 0) + 1
    if auth_header_count >= cookie_count and auth_header_count > 0:
        return {"auth_type": "bearer", "header": "Authorization"}
    if cookie_count > 0:
        return {"auth_type": "cookie", "header": "Cookie"}
    if custom_headers:
        sorted_h = sorted(custom_headers.items(), key=lambda x: -x[1])
        return {"auth_type": "custom_headers", "headers": [h for h, _ in sorted_h]}
    return {"auth_type": "unknown"}


def _detect_envelope(responses: list[dict]) -> dict:
    if not responses:
        return {}
    patterns = [
        {"success_field": "resultCode", "success_values": ["OK", "ok"], "data_fields": ["data", "result"]},
        {"success_field": "code", "success_values": [0, "0", 200], "data_fields": ["data", "result"]},
        {"success_field": "errcode", "success_values": [0, "0"], "data_fields": ["data"]},
        {"success_field": "success", "success_values": [True, "true"], "data_fields": ["data"]},
        {"success_field": "ret", "success_values": [0, "0"], "data_fields": ["data"]},
    ]
    for pat in patterns:
        match_count = 0
        data_field_found = None
        for resp in responses:
            val = resp.get(pat["success_field"])
            if val in pat["success_values"]:
                match_count += 1
                for df in pat["data_fields"]:
                    if df in resp:
                        data_field_found = df
        if match_count >= max(1, len(responses) * 0.3):
            result = {"success_field": pat["success_field"], "success_value": pat["success_values"][0]}
            if data_field_found:
                result["data_field"] = data_field_found
            return result
    return {}


# ---------------------------------------------------------------------------
# HTTP API handler
# ---------------------------------------------------------------------------
class RecorderHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json_response(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._json_response({
                "status": "ok",
                "connected": _state.get("connected", False),
                "chrome_pid": _state.get("chrome_pid"),
                "captured_requests": len(_state["captured_requests"]),
                "user_actions": len(_state["user_actions"]),
            })
            return

        if path == "/requests":
            # Lazy-fetch response bodies before returning
            _fetch_response_bodies()
            params = parse_qs(parsed.query)
            since = float(params.get("since", [0])[0])
            with _lock:
                reqs = [
                    {k: v for k, v in r.items() if not k.startswith("_")}
                    for r in _state["captured_requests"]
                    if r["timestamp"] > since
                ]
            self._json_response({"count": len(reqs), "requests": reqs})
            return

        if path == "/requests/summary":
            with _lock:
                reqs = _state["captured_requests"]
            summary = [{
                "method": r["method"],
                "url": r["url"],
                "status": r["status"],
                "has_request_body": r["request_body"] is not None,
                "has_response_body": r["response_body"] is not None,
            } for r in reqs]
            self._json_response({"count": len(summary), "requests": summary})
            return

        if path == "/requests/clear":
            with _lock:
                _state["captured_requests"].clear()
            self._json_response({"status": "cleared"})
            return

        if path == "/actions":
            params = parse_qs(parsed.query)
            since = float(params.get("since", [0])[0])
            with _lock:
                actions = [a for a in _state["user_actions"]
                           if a.get("timestamp", 0) / 1000 > since]
            self._json_response({"count": len(actions), "actions": actions})
            return

        if path == "/actions/clear":
            with _lock:
                _state["user_actions"].clear()
            self._json_response({"status": "cleared"})
            return

        if path == "/actions/summary":
            with _lock:
                actions = _state["user_actions"]
            summary = []
            for a in actions:
                s = {"type": a.get("type"), "timestamp": a.get("timestamp")}
                el = a.get("element", {})
                if el:
                    s["element"] = el.get("selector", el.get("tag", "?"))
                    if el.get("text"):
                        s["text"] = el["text"][:50]
                if a.get("value"):
                    s["value"] = a["value"][:50]
                summary.append(s)
            self._json_response({"count": len(summary), "actions": summary})
            return

        if path == "/navigations":
            with _lock:
                navs = list(_state["page_navigations"])
            self._json_response({"count": len(navs), "navigations": navs})
            return

        if path == "/trace-params":
            _fetch_response_bodies()
            with _lock:
                reqs = list(_state["captured_requests"])
            trace = _trace_param_sources(reqs)
            self._json_response(trace)
            return

        if path == "/console":
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", [50])[0])
            with _lock:
                msgs = _state["console_messages"][-limit:]
            self._json_response({"count": len(msgs), "messages": msgs})
            return

        # CDP pass-through: evaluate JS, get storage, etc.
        if path == "/page-info":
            result = _send_cdp("Runtime.evaluate", {
                "expression": "JSON.stringify({url: location.href, title: document.title})",
                "returnByValue": True,
            })
            try:
                val = json.loads(result.get("result", {}).get("value", "{}"))
                self._json_response(val)
            except Exception:
                self._json_response(result)
            return

        if path == "/storage":
            cookies_result = _send_cdp("Network.getCookies")
            ls_result = _send_cdp("Runtime.evaluate", {
                "expression": """JSON.stringify((() => {
                    const items = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        items[key] = localStorage.getItem(key);
                    }
                    return items;
                })())""",
                "returnByValue": True,
            })
            ss_result = _send_cdp("Runtime.evaluate", {
                "expression": """JSON.stringify((() => {
                    const items = {};
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const key = sessionStorage.key(i);
                        items[key] = sessionStorage.getItem(key);
                    }
                    return items;
                })())""",
                "returnByValue": True,
            })
            self._json_response({
                "cookies": cookies_result.get("cookies", []),
                "localStorage": json.loads(ls_result.get("result", {}).get("value", "{}")),
                "sessionStorage": json.loads(ss_result.get("result", {}).get("value", "{}")),
            })
            return

        if path == "/screenshot":
            result = _send_cdp("Page.captureScreenshot", {"format": "png"})
            if "data" in result:
                import base64
                sd = Path(_state["state_dir"]) / "screenshots"
                sd.mkdir(exist_ok=True)
                count = len(list(sd.glob("*.png"))) + 1
                ss_path = sd / f"screenshot_{count:04d}.png"
                ss_path.write_bytes(base64.b64decode(result["data"]))
                self._json_response({"path": str(ss_path)})
            else:
                self._json_response({"error": "Screenshot failed", "details": result}, 500)
            return

        if path == "/elements":
            params_qs = parse_qs(parsed.query)
            selector = params_qs.get("selector", ["a, button, input, select, textarea, [role='button'], [onclick]"])[0]
            result = _send_cdp("Runtime.evaluate", {
                "expression": f"""JSON.stringify((() => {{
                    const els = document.querySelectorAll({json.dumps(selector)});
                    return Array.from(els).slice(0, 100).map((el, i) => {{
                        const rect = el.getBoundingClientRect();
                        return {{
                            index: i,
                            tag: el.tagName.toLowerCase(),
                            type: el.type || null,
                            id: el.id || null,
                            name: el.name || null,
                            className: el.className ? el.className.toString().slice(0, 80) : null,
                            text: (el.textContent || '').trim().slice(0, 80),
                            placeholder: el.placeholder || null,
                            href: el.href || null,
                            value: el.value || null,
                            visible: rect.width > 0 && rect.height > 0,
                            rect: {{x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)}},
                        }};
                    }});
                }})())""",
                "returnByValue": True,
            })
            try:
                elements = json.loads(result.get("result", {}).get("value", "[]"))
                self._json_response({"count": len(elements), "elements": elements})
            except Exception:
                self._json_response({"error": "Failed to list elements", "details": result}, 500)
            return

        if path == "/analyze-js":
            result = _send_cdp("Runtime.evaluate", {
                "expression": """JSON.stringify((() => {
                    const findings = {scripts: [], api_patterns: []};
                    const scripts = document.querySelectorAll('script');
                    scripts.forEach(s => {
                        if (s.src) {
                            findings.scripts.push({type: 'external', src: s.src});
                        } else if (s.textContent.length > 0 && s.textContent.length < 50000) {
                            const text = s.textContent;
                            const patterns = [
                                /fetch\\s*\\(\\s*["'`]([^"'`]*(?:api|\\/v\\d)[^"'`]*)["'`]/gi,
                                /axios\\s*\\.\\s*(get|post|put|delete|patch)\\s*\\(\\s*["'`]([^"'`]+)["'`]/gi,
                                /\\$\\s*\\.\\s*(ajax|get|post)\\s*\\(\\s*["'`]([^"'`]+)["'`]/gi,
                                /(?:url|endpoint|path|api)\\s*[:=]\\s*["'`]([^"'`]*\\/api\\/[^"'`]*)["'`]/gi,
                            ];
                            patterns.forEach(pat => {
                                let m;
                                while ((m = pat.exec(text)) !== null) {
                                    findings.api_patterns.push({
                                        match: m[0].slice(0, 200),
                                        context: text.slice(Math.max(0, m.index - 100), m.index + m[0].length + 100).trim(),
                                    });
                                }
                            });
                        }
                    });
                    try {
                        if (window.axios && window.axios.defaults) {
                            findings.api_patterns.push({
                                match: 'axios.defaults.baseURL = ' + (window.axios.defaults.baseURL || 'undefined'),
                                context: JSON.stringify({
                                    baseURL: window.axios.defaults.baseURL,
                                    headers: window.axios.defaults.headers?.common || {},
                                }).slice(0, 500),
                            });
                        }
                    } catch(e) {}
                    return findings;
                })())""",
                "returnByValue": True,
            })
            try:
                val = json.loads(result.get("result", {}).get("value", "{}"))
                self._json_response(val)
            except Exception:
                self._json_response(result)
            return

        self._json_response({"error": f"Unknown route: {path}"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
        data = json.loads(body) if body else {}

        if path == "/export-catalog":
            _fetch_response_bodies()
            with _lock:
                reqs = list(_state["captured_requests"])
            catalog = _build_catalog(reqs, data.get("site_name", ""))
            sd = Path(_state["state_dir"])
            catalog_path = sd / "api_catalog.json"
            catalog_path.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            self._json_response({"path": str(catalog_path), "catalog": catalog})
            return

        if path == "/evaluate":
            expression = data.get("expression", "")
            if not expression:
                self._json_response({"error": "expression is required"}, 400)
                return
            result = _send_cdp("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
            })
            self._json_response({"result": result.get("result", {}).get("value")})
            return

        if path == "/search-js":
            pattern = data.get("pattern", "")
            max_context = data.get("context_chars", 300)
            if not pattern:
                self._json_response({"error": "pattern is required"}, 400)
                return
            result = _send_cdp("Runtime.evaluate", {
                "expression": f"""(async function() {{
                    const pattern = {json.dumps(pattern)};
                    const maxCtx = {max_context};
                    const findings = [];
                    const scripts = document.querySelectorAll('script:not([src])');
                    scripts.forEach((s, idx) => {{
                        const text = s.textContent;
                        let pos = -1;
                        while ((pos = text.indexOf(pattern, pos + 1)) !== -1) {{
                            const start = Math.max(0, pos - maxCtx);
                            const end = Math.min(text.length, pos + pattern.length + maxCtx);
                            findings.push({{
                                source: 'inline_script_' + idx,
                                position: pos,
                                context: text.slice(start, end),
                            }});
                            if (findings.length >= 20) break;
                        }}
                    }});
                    if (findings.length < 20) {{
                        const extScripts = Array.from(document.querySelectorAll('script[src]'))
                            .map(s => s.src)
                            .filter(s => !s.includes('vendor') && !s.includes('polyfill'));
                        for (const src of extScripts.slice(0, 10)) {{
                            try {{
                                const resp = await fetch(src);
                                const text = await resp.text();
                                let pos = -1;
                                while ((pos = text.indexOf(pattern, pos + 1)) !== -1) {{
                                    const start = Math.max(0, pos - maxCtx);
                                    const end = Math.min(text.length, pos + pattern.length + maxCtx);
                                    findings.push({{
                                        source: src.split('/').pop(),
                                        position: pos,
                                        context: text.slice(start, end),
                                    }});
                                    if (findings.length >= 20) break;
                                }}
                            }} catch(e) {{}}
                            if (findings.length >= 20) break;
                        }}
                    }}
                    return JSON.stringify(findings);
                }})()""",
                "returnByValue": True,
                "awaitPromise": True,
            }, timeout=30)
            try:
                findings = json.loads(result.get("result", {}).get("value", "[]"))
                self._json_response({"pattern": pattern, "count": len(findings), "findings": findings})
            except Exception:
                self._json_response({"pattern": pattern, "count": 0, "findings": [], "raw": result})
            return

        if path == "/reinject-observer":
            _inject_observer()
            self._json_response({"status": "ok", "message": "Observer re-injected"})
            return

        if path == "/close":
            _state["running"] = False
            pid = _state.get("chrome_pid")
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self._json_response({"status": "closing"})
            threading.Thread(target=self._shutdown_server, daemon=True).start()
            return

        # Timeline: combined view of actions + requests in chronological order
        if path == "/timeline":
            with _lock:
                reqs = list(_state["captured_requests"])
                actions = list(_state["user_actions"])

            events = []
            for r in reqs:
                events.append({
                    "time": r["timestamp"],
                    "type": "request",
                    "method": r["method"],
                    "url": r["url"],
                    "status": r["status"],
                })
            for a in actions:
                ts = a.get("timestamp", 0) / 1000  # JS timestamp is in ms
                events.append({
                    "time": ts,
                    "type": "action",
                    "action": a.get("type"),
                    "element": a.get("element", {}).get("selector", "?"),
                    "text": a.get("element", {}).get("text", "")[:50],
                    "value": (a.get("value") or "")[:50],
                })

            events.sort(key=lambda e: e["time"])
            self._json_response({"count": len(events), "events": events})
            return

        self._json_response({"error": f"Unknown route: {path}"}, 404)

    def _shutdown_server(self):
        time.sleep(0.5)
        self.server.shutdown()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Chrome CDP Recorder — passive browser session recording")
    parser.add_argument("--url", required=True, help="Initial URL to open in Chrome")
    parser.add_argument("--port", type=int, default=8766, help="HTTP API port (default: 8766)")
    parser.add_argument("--debug-port", type=int, default=9222, help="Chrome remote debugging port (default: 9222)")
    parser.add_argument("--state-dir", default="/tmp/cli-everything", help="State directory")
    parser.add_argument("--no-launch", action="store_true",
                        help="Don't launch Chrome, connect to an already running instance")
    parser.add_argument("--user-data-dir", default=None,
                        help="Chrome user data directory (for reusing existing profile/cookies)")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "screenshots").mkdir(exist_ok=True)
    _state["state_dir"] = str(state_dir)

    # Clear old logs
    for f in ["requests.jsonl", "actions.jsonl"]:
        p = state_dir / f
        if p.exists():
            p.unlink()

    # Launch Chrome (unless --no-launch)
    chrome_proc = None
    if not args.no_launch:
        chrome_proc = _launch_chrome(args.url, args.debug_port, args.user_data_dir)

    # Get WebSocket URL
    print(f"[recorder] Waiting for Chrome on port {args.debug_port}...", file=sys.stderr)
    try:
        ws_url = _get_ws_url(args.debug_port)
    except RuntimeError as e:
        print(f"[recorder] Error: {e}", file=sys.stderr)
        if chrome_proc:
            chrome_proc.terminate()
        sys.exit(1)

    print(f"[recorder] Chrome connected: {ws_url}", file=sys.stderr)
    _state["ws_url"] = ws_url
    _state["debug_port"] = args.debug_port

    # Start CDP listener thread
    cdp_thread = threading.Thread(target=_run_cdp, args=(ws_url, str(state_dir)), daemon=True)
    cdp_thread.start()

    # Start background body fetcher thread
    body_thread = threading.Thread(target=_body_fetcher_loop, daemon=True)
    body_thread.start()

    # Wait for CDP connection
    for _ in range(20):
        if _state.get("connected"):
            break
        time.sleep(0.5)
    else:
        print("[recorder] Timeout waiting for CDP connection", file=sys.stderr)
        sys.exit(1)

    # Save server info
    (state_dir / "server.json").write_text(json.dumps({
        "port": args.port,
        "pid": os.getpid(),
        "chrome_pid": _state.get("chrome_pid"),
        "debug_port": args.debug_port,
        "url": args.url,
        "mode": "recorder",
    }))

    print(f"", file=sys.stderr)
    print(f"=== Chrome CDP Recorder ===", file=sys.stderr)
    print(f"  Browser is open. Operate it manually.", file=sys.stderr)
    print(f"  All network requests and user actions are being recorded.", file=sys.stderr)
    print(f"  HTTP API: http://localhost:{args.port}", file=sys.stderr)
    print(f"  State dir: {state_dir}", file=sys.stderr)
    print(f"", file=sys.stderr)

    def _shutdown(sig, frame):
        _state["running"] = False
        if chrome_proc:
            chrome_proc.terminate()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)

    server = HTTPServer(("127.0.0.1", args.port), RecorderHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _state["running"] = False
        if chrome_proc:
            chrome_proc.terminate()
        server.server_close()


if __name__ == "__main__":
    main()

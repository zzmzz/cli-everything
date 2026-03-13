#!/usr/bin/env python3
"""Browser automation server for API capture.

Launches a Playwright Chromium browser and exposes an HTTP API for the agent
to control the browser and capture network requests in real-time.

Uses a command-queue pattern: the HTTP thread enqueues commands and the
Playwright thread (which owns the browser) dequeues and executes them.

Usage:
    python browser_server.py --url <url> [--port 8766] [--state-dir /tmp/cli-everything]
"""

import argparse
import json
import os
import queue
import re
import signal
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Command queue for cross-thread communication
# ---------------------------------------------------------------------------
_cmd_queue: queue.Queue = queue.Queue()

# Shared state (written by PW thread, read by HTTP thread)
_state: dict[str, Any] = {
    "page_ready": False,
    "captured_requests": [],
    "state_dir": "/tmp/cli-everything",
    "screenshot_counter": 0,
    "running": True,
}
_lock = threading.Lock()

# Static resource extensions to ignore
STATIC_EXTENSIONS = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".webp", ".avif",
    ".mp4", ".mp3", ".webm", ".ogg", ".xml",
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
# Playwright thread — owns the browser, processes commands from queue
# ---------------------------------------------------------------------------
def _run_playwright(url: str, state_dir: str, headless: bool = False):
    from playwright.sync_api import sync_playwright

    def _capture_response(response):
        try:
            request = response.request
            req_url = request.url
            if _is_static(req_url):
                return
            resource_type = request.resource_type
            if resource_type not in ("xhr", "fetch"):
                return

            resp_body = None
            try:
                resp_body = response.json()
            except Exception:
                try:
                    resp_body = response.text()
                    if len(resp_body) > 5000:
                        resp_body = resp_body[:5000] + "...(truncated)"
                except Exception:
                    pass

            req_body = None
            try:
                req_body = request.post_data
                if req_body:
                    try:
                        req_body = json.loads(req_body)
                    except (json.JSONDecodeError, TypeError):
                        pass
            except Exception:
                pass

            req_headers = {}
            try:
                req_headers = request.headers
            except Exception:
                pass

            entry = {
                "timestamp": time.time(),
                "method": request.method,
                "url": req_url,
                "resource_type": resource_type,
                "status": response.status,
                "request_headers": req_headers,
                "request_body": req_body,
                "response_body": resp_body,
            }

            with _lock:
                _state["captured_requests"].append(entry)
                sd = Path(state_dir)
                with open(sd / "requests.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            print(f"[capture] Error: {e}", file=sys.stderr)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--window-size=1280,900"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()
        page.on("response", _capture_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[playwright] Navigation error: {e}", file=sys.stderr)

        _state["page_ready"] = True
        print(f"[playwright] Page ready: {url}", file=sys.stderr)

        # Main command loop
        while _state["running"]:
            try:
                cmd_name, cmd_data, result_q = _cmd_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            result = _execute_command(cmd_name, cmd_data, page, context, browser, state_dir)
            result_q.put(result)

            # Check if we need to update the page reference (e.g., after new-tab)
            if cmd_name == "new-tab" and "new_page" in result:
                page = result.pop("new_page")

            if cmd_name == "switch-tab" and "new_page" in result:
                page = result.pop("new_page")

        browser.close()


def _execute_command(
    cmd: str, data: dict, page, context, browser, state_dir: str
) -> dict:
    """Execute a browser command in the Playwright thread."""
    try:
        if cmd == "page-info":
            return {"url": page.url, "title": page.title()}

        elif cmd == "screenshot":
            _state["screenshot_counter"] += 1
            sd = Path(state_dir) / "screenshots"
            sd.mkdir(exist_ok=True)
            ss_path = sd / f"screenshot_{_state['screenshot_counter']:04d}.png"
            page.screenshot(path=str(ss_path), full_page=False)
            return {"path": str(ss_path)}

        elif cmd == "elements":
            selector = data.get(
                "selector",
                "a, button, input, select, textarea, [role='button'], [onclick]",
            )
            elements = page.evaluate("""(selector) => {
                const els = document.querySelectorAll(selector);
                return Array.from(els).slice(0, 100).map((el, i) => {
                    const rect = el.getBoundingClientRect();
                    return {
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
                        rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)},
                    };
                });
            }""", selector)
            return {"count": len(elements), "elements": elements}

        elif cmd == "navigate":
            page.goto(data["url"], wait_until="domcontentloaded", timeout=30000)
            return {"url": page.url, "title": page.title()}

        elif cmd == "click":
            page.click(data["selector"], timeout=10000)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            return {"url": page.url, "title": page.title()}

        elif cmd == "fill":
            page.fill(data["selector"], data["value"], timeout=10000)
            return {"status": "ok"}

        elif cmd == "type":
            page.type(
                data["selector"],
                data["text"],
                delay=data.get("delay", 50),
                timeout=10000,
            )
            return {"status": "ok"}

        elif cmd == "press":
            if data.get("selector"):
                page.press(data["selector"], data["key"], timeout=10000)
            else:
                page.keyboard.press(data["key"])
            return {"status": "ok"}

        elif cmd == "select":
            page.select_option(data["selector"], data["value"], timeout=10000)
            return {"status": "ok"}

        elif cmd == "scroll":
            page.evaluate(f"window.scrollBy({data.get('x', 0)}, {data.get('y', 300)})")
            return {"status": "ok"}

        elif cmd == "evaluate":
            result = page.evaluate(data["expression"])
            return {"result": result}

        elif cmd == "wait":
            page.wait_for_selector(
                data["selector"],
                state=data.get("state", "visible"),
                timeout=data.get("timeout", 10000),
            )
            return {"status": "ok"}

        elif cmd == "wait-for-navigation":
            page.wait_for_load_state(
                "networkidle", timeout=data.get("timeout", 10000)
            )
            return {"url": page.url, "title": page.title()}

        elif cmd == "new-tab":
            new_page = context.new_page()
            new_page.on("response", page._listeners.get("response", [None])[0] or (lambda r: None))
            tab_url = data.get("url", "about:blank")
            new_page.goto(tab_url, wait_until="domcontentloaded", timeout=30000)
            return {"url": new_page.url, "title": new_page.title(), "new_page": new_page}

        elif cmd == "switch-tab":
            index = data.get("index", 0)
            pages = context.pages
            if 0 <= index < len(pages):
                return {
                    "url": pages[index].url,
                    "title": pages[index].title(),
                    "total_tabs": len(pages),
                    "new_page": pages[index],
                }
            else:
                return {"error": f"Tab index {index} out of range (0-{len(pages)-1})"}

        elif cmd == "storage":
            # Dump cookies, localStorage, sessionStorage
            cookies = context.cookies()
            local_storage = page.evaluate("""() => {
                const items = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    items[key] = localStorage.getItem(key);
                }
                return items;
            }""")
            session_storage = page.evaluate("""() => {
                const items = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    items[key] = sessionStorage.getItem(key);
                }
                return items;
            }""")
            return {
                "cookies": cookies,
                "localStorage": local_storage,
                "sessionStorage": session_storage,
            }

        elif cmd == "analyze-js":
            # In-browser JS analysis: find how API calls are constructed
            query = data.get("query", "")  # optional: search for specific API path
            result = page.evaluate("""(query) => {
                const findings = {scripts: [], api_patterns: [], intercepted_calls: []};

                // 1. Collect all inline + external script URLs
                const scripts = document.querySelectorAll('script');
                scripts.forEach(s => {
                    if (s.src) {
                        findings.scripts.push({type: 'external', src: s.src});
                    } else if (s.textContent.length > 0 && s.textContent.length < 50000) {
                        // Analyze inline scripts for API patterns
                        const text = s.textContent;
                        const patterns = [
                            // fetch calls
                            /fetch\s*\(\s*["'`]([^"'`]*(?:api|\/v\d)[^"'`]*)["'`]/gi,
                            // axios calls
                            /axios\s*\.\s*(get|post|put|delete|patch)\s*\(\s*["'`]([^"'`]+)["'`]/gi,
                            // $.ajax / $.get / $.post
                            /\$\s*\.\s*(ajax|get|post)\s*\(\s*["'`]([^"'`]+)["'`]/gi,
                            // URL string assignments containing /api/
                            /(?:url|endpoint|path|api)\s*[:=]\s*["'`]([^"'`]*\/api\/[^"'`]*)["'`]/gi,
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

                // 2. Check for global API config objects
                const configNames = [
                    'API_BASE', 'API_URL', 'BASE_URL', 'apiBase', 'apiUrl',
                    'baseURL', 'API_HOST', 'apiHost', '__API__', 'config',
                ];
                configNames.forEach(name => {
                    try {
                        const val = eval(name);
                        if (val && typeof val === 'string') {
                            findings.api_patterns.push({match: name + ' = ' + val, context: 'global variable'});
                        } else if (val && typeof val === 'object' && !Array.isArray(val)) {
                            // Look for URL-like values in config objects
                            const interesting = {};
                            for (const [k, v] of Object.entries(val)) {
                                if (typeof v === 'string' && (v.includes('http') || v.includes('/api'))) {
                                    interesting[k] = v;
                                }
                            }
                            if (Object.keys(interesting).length > 0) {
                                findings.api_patterns.push({match: name, context: JSON.stringify(interesting).slice(0, 500)});
                            }
                        }
                    } catch(e) {}
                });

                // 3. Intercept XMLHttpRequest.open and fetch to see how params are built
                // Check if there are axios instances with interceptors
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

                // 4. If query provided, search all loaded script text for it
                if (query) {
                    const allScripts = performance.getEntriesByType('resource')
                        .filter(r => r.initiatorType === 'script')
                        .map(r => r.name);
                    findings.scripts_to_analyze = allScripts.filter(s => !s.includes('vendor') && !s.includes('chunk-vendors'));
                }

                return findings;
            }""", query)
            return result

        elif cmd == "hook-xhr":
            # Install XHR/fetch hooks to capture param construction in real-time
            page.evaluate("""() => {
                if (window.__cli_everything_hooked) return 'already hooked';
                window.__cli_everything_hooked = true;
                window.__cli_everything_calls = [];

                // Hook fetch
                const origFetch = window.fetch;
                window.fetch = function(input, init) {
                    const entry = {
                        type: 'fetch',
                        timestamp: Date.now(),
                        url: typeof input === 'string' ? input : input.url,
                        method: (init && init.method) || 'GET',
                        headers: (init && init.headers) ? JSON.parse(JSON.stringify(init.headers)) : {},
                        body: (init && init.body) ? String(init.body).slice(0, 2000) : null,
                        stack: new Error().stack.split('\\n').slice(1, 6).map(s => s.trim()),
                    };
                    window.__cli_everything_calls.push(entry);
                    return origFetch.apply(this, arguments);
                };

                // Hook XMLHttpRequest
                const origOpen = XMLHttpRequest.prototype.open;
                const origSend = XMLHttpRequest.prototype.send;
                const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;

                XMLHttpRequest.prototype.open = function(method, url) {
                    this.__cli_info = {type: 'xhr', method, url, headers: {}, timestamp: Date.now()};
                    return origOpen.apply(this, arguments);
                };
                XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
                    if (this.__cli_info) this.__cli_info.headers[name] = value;
                    return origSetHeader.apply(this, arguments);
                };
                XMLHttpRequest.prototype.send = function(body) {
                    if (this.__cli_info) {
                        this.__cli_info.body = body ? String(body).slice(0, 2000) : null;
                        this.__cli_info.stack = new Error().stack.split('\\n').slice(1, 6).map(s => s.trim());
                        window.__cli_everything_calls.push(this.__cli_info);
                    }
                    return origSend.apply(this, arguments);
                };

                return 'hooks installed';
            }""")
            return {"status": "ok", "message": "XHR/fetch hooks installed — call stacks will be captured"}

        elif cmd == "get-hooked-calls":
            # Retrieve calls captured by the XHR/fetch hooks (includes call stacks)
            calls = page.evaluate("""() => {
                const calls = window.__cli_everything_calls || [];
                const result = calls.splice(0);  // drain the queue
                return result;
            }""")
            return {"count": len(calls), "calls": calls}

        elif cmd == "search-js":
            # Search loaded JS bundles for a specific pattern (e.g., an API path)
            pattern = data.get("pattern", "")
            max_context = data.get("context_chars", 300)
            if not pattern:
                return {"error": "pattern is required"}
            # Fetch all script sources via CDP-style evaluation
            results = page.evaluate("""async ({pattern, maxCtx}) => {
                const findings = [];
                // Search inline scripts
                const scripts = document.querySelectorAll('script:not([src])');
                scripts.forEach((s, idx) => {
                    const text = s.textContent;
                    let pos = -1;
                    while ((pos = text.indexOf(pattern, pos + 1)) !== -1) {
                        const start = Math.max(0, pos - maxCtx);
                        const end = Math.min(text.length, pos + pattern.length + maxCtx);
                        findings.push({
                            source: 'inline_script_' + idx,
                            position: pos,
                            context: text.slice(start, end),
                        });
                        if (findings.length >= 20) break;
                    }
                    if (findings.length >= 20) return;
                });

                // Search external scripts (fetch their content)
                if (findings.length < 20) {
                    const extScripts = Array.from(document.querySelectorAll('script[src]'))
                        .map(s => s.src)
                        .filter(s => !s.includes('vendor') && !s.includes('polyfill') && !s.includes('chunk-vendors'));
                    for (const src of extScripts.slice(0, 10)) {
                        try {
                            const resp = await fetch(src);
                            const text = await resp.text();
                            let pos = -1;
                            while ((pos = text.indexOf(pattern, pos + 1)) !== -1) {
                                const start = Math.max(0, pos - maxCtx);
                                const end = Math.min(text.length, pos + pattern.length + maxCtx);
                                findings.push({
                                    source: src.split('/').pop(),
                                    position: pos,
                                    context: text.slice(start, end),
                                });
                                if (findings.length >= 20) break;
                            }
                        } catch(e) {}
                        if (findings.length >= 20) break;
                    }
                }
                return findings;
            }""", {"pattern": pattern, "maxCtx": max_context})
            return {"pattern": pattern, "count": len(results), "findings": results}

        elif cmd == "close":
            _state["running"] = False
            return {"status": "closing"}

        else:
            return {"error": f"Unknown command: {cmd}"}

    except Exception as e:
        return {"error": str(e)}


def _send_command(cmd: str, data: dict = None, timeout: float = 30) -> dict:
    """Send a command to the Playwright thread and wait for result."""
    result_q: queue.Queue = queue.Queue()
    _cmd_queue.put((cmd, data or {}, result_q))
    try:
        return result_q.get(timeout=timeout)
    except queue.Empty:
        return {"error": f"Command '{cmd}' timed out after {timeout}s"}


# ---------------------------------------------------------------------------
# Catalog builder
# ---------------------------------------------------------------------------
from collections import Counter, defaultdict


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

    json_responses = [
        r["response_body"] for r in requests if isinstance(r.get("response_body"), dict)
    ]
    envelope = _detect_envelope(json_responses)

    _dynamic_seg = re.compile(
        r"^([0-9a-f]{8,}|[0-9a-f]{8}-[0-9a-f]{4}-|\d{6,})$", re.IGNORECASE
    )
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
        if "authorization" in hdrs:
            auth_header_count += 1
        if "cookie" in hdrs:
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
# Parameter provenance tracing
# ---------------------------------------------------------------------------
def _extract_values_flat(obj, prefix="") -> dict[str, str]:
    """Recursively extract all leaf values from a dict/list as {path: str_value}."""
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
        if len(s) >= 4:  # ignore trivially short values like "0", "1", "OK"
            result[prefix or "value"] = s
    return result


def _trace_param_sources(requests: list[dict]) -> dict:
    """Trace where each request parameter came from.

    For every parameter in request N, check if its value appeared in:
      - An earlier response body (response_field → request_param dependency)
      - A URL query param of an earlier request/response
      - A cookie / header of an earlier request

    Returns a list of traced dependencies per request.
    """
    traced: list[dict] = []

    # Build a cumulative pool of "known values" from all prior responses
    # Each entry: {value_str: [{source_request_index, source_path}]}
    known_values: dict[str, list[dict]] = {}

    for i, req in enumerate(requests):
        req_params = {}

        # Extract params from request body
        if req.get("request_body"):
            req_params.update(_extract_values_flat(req["request_body"], "body"))

        # Extract params from URL query string
        parsed = urlparse(req["url"])
        if parsed.query:
            for k, vs in parse_qs(parsed.query).items():
                for v in vs:
                    if len(v) >= 4:
                        req_params[f"query.{k}"] = v

        # Extract non-standard headers that might carry dynamic values
        ignore_headers = {
            "accept", "content-type", "user-agent", "origin", "referer",
            "accept-language", "accept-encoding", "connection", "host",
            "content-length", "pragma", "cache-control", "sec-ch-ua",
            "sec-ch-ua-mobile", "sec-ch-ua-platform", "sec-fetch-dest",
            "sec-fetch-mode", "sec-fetch-site",
        }
        for hdr, val in req.get("request_headers", {}).items():
            if hdr.lower() not in ignore_headers and len(val) >= 6:
                req_params[f"header.{hdr}"] = val

        # Now match each param value against known_values pool
        dependencies = []
        for param_path, param_val in req_params.items():
            if param_val in known_values:
                sources = known_values[param_val]
                for src in sources[:3]:  # limit to 3 matches
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

        # Add this response's values to the known_values pool
        if req.get("response_body"):
            resp_vals = _extract_values_flat(req["response_body"], "response")
            for path, val in resp_vals.items():
                if val not in known_values:
                    known_values[val] = []
                known_values[val].append({"index": i, "path": path})

        # Also add URL as a known value source
        if parsed.query:
            for k, vs in parse_qs(parsed.query).items():
                for v in vs:
                    if len(v) >= 4 and v not in known_values:
                        known_values[v] = []
                    if v in known_values:
                        known_values[v].append({"index": i, "path": f"url.query.{k}"})

    # Filter out requests with no dependencies for readability
    return {
        "total_requests": len(requests),
        "traced": [t for t in traced if t["dependencies"]],
        "all_traced": traced,
    }


# ---------------------------------------------------------------------------
# HTTP handler — dispatches to PW thread via _send_command
# ---------------------------------------------------------------------------
class BrowserHandler(BaseHTTPRequestHandler):
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
            self._json_response({"status": "ok", "page_ready": _state.get("page_ready", False)})
            return

        if path == "/requests":
            params = parse_qs(parsed.query)
            since = float(params.get("since", [0])[0])
            with _lock:
                reqs = [r for r in _state["captured_requests"] if r["timestamp"] > since]
            self._json_response({"count": len(reqs), "requests": reqs})
            return

        if path == "/requests/summary":
            with _lock:
                reqs = _state["captured_requests"]
            summary = [
                {
                    "method": r["method"],
                    "url": r["url"],
                    "status": r["status"],
                    "has_request_body": r["request_body"] is not None,
                    "has_response_body": r["response_body"] is not None,
                }
                for r in reqs
            ]
            self._json_response({"count": len(summary), "requests": summary})
            return

        if path == "/requests/clear":
            with _lock:
                _state["captured_requests"].clear()
            self._json_response({"status": "cleared"})
            return

        if path == "/trace-params":
            # Analyze parameter provenance across the request chain
            with _lock:
                reqs = list(_state["captured_requests"])
            trace = _trace_param_sources(reqs)
            self._json_response(trace)
            return

        # Commands that need Playwright thread
        if path in ("/screenshot", "/page-info", "/elements", "/storage",
                     "/analyze-js", "/get-hooked-calls"):
            cmd = path.lstrip("/")
            params = parse_qs(parsed.query)
            data = {}
            if "selector" in params:
                data["selector"] = params["selector"][0]
            result = _send_command(cmd, data)
            status = 500 if "error" in result else 200
            self._json_response(result, status)
            return

        self._json_response({"error": f"Unknown route: {path}"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
        data = json.loads(body) if body else {}

        if path == "/export-catalog":
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

        # All other POST commands go to Playwright thread
        cmd = path.lstrip("/")
        result = _send_command(cmd, data)

        # Handle close specially
        if cmd == "close":
            self._json_response(result)
            threading.Thread(target=self._shutdown_server, daemon=True).start()
            return

        status = 500 if "error" in result else 200
        self._json_response(result, status)

    def _shutdown_server(self):
        time.sleep(0.5)
        self.server.shutdown()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Browser automation server for API capture")
    parser.add_argument("--url", required=True, help="Initial URL to navigate to")
    parser.add_argument("--port", type=int, default=8766, help="HTTP server port (default: 8766)")
    parser.add_argument("--state-dir", default="/tmp/cli-everything", help="State directory")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "screenshots").mkdir(exist_ok=True)
    _state["state_dir"] = str(state_dir)

    requests_log = state_dir / "requests.jsonl"
    if requests_log.exists():
        requests_log.unlink()

    pw_thread = threading.Thread(
        target=_run_playwright,
        args=(args.url, str(state_dir), args.headless),
        daemon=True,
    )
    pw_thread.start()

    print(f"Starting browser, navigating to {args.url}...", file=sys.stderr)
    for _ in range(60):
        if _state.get("page_ready"):
            break
        time.sleep(0.5)
    else:
        print("Timeout waiting for browser to start", file=sys.stderr)
        sys.exit(1)

    print(f"Browser ready. HTTP API on http://localhost:{args.port}", file=sys.stderr)
    print(f"State directory: {state_dir}", file=sys.stderr)

    (state_dir / "server.json").write_text(json.dumps({
        "port": args.port,
        "pid": os.getpid(),
        "url": args.url,
    }))

    def _shutdown(sig, frame):
        _state["running"] = False
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)

    server = HTTPServer(("127.0.0.1", args.port), BrowserHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _state["running"] = False
        server.server_close()


if __name__ == "__main__":
    main()

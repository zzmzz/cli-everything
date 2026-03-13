#!/usr/bin/env python3
"""HAR file parser — extracts API catalog from browser-captured HAR files.

Usage:
    python har_parser.py <har-file> [--output <json-file>] [--site-name <name>]

Reads a .har file exported from Chrome DevTools and produces a structured
API catalog JSON suitable for CLI code generation.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# Extensions to exclude (static resources)
STATIC_EXTENSIONS = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".webp", ".avif",
    ".mp4", ".mp3", ".webm", ".ogg",
}

# MIME types indicating API responses
API_MIME_TYPES = {"application/json", "application/xml", "text/json", "text/xml"}

# Resource types that indicate XHR/Fetch
XHR_RESOURCE_TYPES = {"xhr", "fetch"}


def load_har(path: str) -> dict:
    """Load and validate a HAR file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "log" not in data or "entries" not in data["log"]:
        raise ValueError("Invalid HAR file: missing log.entries")
    return data


# URL path patterns that indicate non-API resources
STATIC_PATH_PATTERNS = re.compile(
    r"(/static/|/assets/|/media/|/lottie/|/dist/|/build/|/public/|/images/)",
    re.IGNORECASE,
)


def _is_static_resource(url: str) -> bool:
    """Check if URL points to a static resource."""
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in STATIC_EXTENSIONS):
        return True
    if STATIC_PATH_PATTERNS.search(path_lower):
        return True
    return False


def _get_content_type(headers: list[dict]) -> str:
    """Extract Content-Type from headers list."""
    for h in headers:
        if h.get("name", "").lower() == "content-type":
            return h.get("value", "")
    return ""


def _is_api_response(entry: dict) -> bool:
    """Determine if an entry is an API call (not a static resource)."""
    url = entry["request"]["url"]
    if _is_static_resource(url):
        return False

    # Check _resourceType if available (Chrome HAR)
    resource_type = entry.get("_resourceType", "").lower()
    if resource_type and resource_type in XHR_RESOURCE_TYPES:
        return True

    # Check response content type
    resp_content_type = _get_content_type(
        entry.get("response", {}).get("headers", [])
    )
    if any(mt in resp_content_type for mt in API_MIME_TYPES):
        return True

    # Check if request has JSON content type
    req_content_type = _get_content_type(
        entry.get("request", {}).get("headers", [])
    )
    if "application/json" in req_content_type:
        return True

    return False


def _parse_post_data(post_data: dict | None) -> Any:
    """Parse request post data."""
    if not post_data:
        return None
    text = post_data.get("text", "")
    mime = post_data.get("mimeType", "")
    if "json" in mime:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
    # Form data
    params = post_data.get("params", [])
    if params:
        return {p["name"]: p.get("value", "") for p in params}
    return text if text else None


def _parse_response_body(content: dict) -> Any:
    """Parse response body content."""
    text = content.get("text", "")
    mime = content.get("mimeType", "")
    if not text:
        return None
    if "json" in mime:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
    return text


def _extract_auth_info(entries: list[dict]) -> dict:
    """Detect authentication method from request headers."""
    cookie_count = 0
    auth_header_count = 0
    token_in_body_count = 0
    custom_header_counts: dict[str, int] = {}

    # Headers that are standard / not auth-related
    IGNORE_HEADERS = {
        "accept", "content-type", "user-agent", "origin", "referer",
        "accept-language", "accept-encoding", "connection", "host",
        "content-length", "pragma", "cache-control", "dnt", "expires",
        "priority", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
        "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
        "access-control-request-headers", "access-control-request-method",
        ":authority", ":method", ":path", ":scheme",
    }
    # Custom header patterns that likely carry auth/identity
    AUTH_HEADER_PATTERNS = re.compile(
        r"(token|secret|signature|session|auth)", re.IGNORECASE,
    )

    for entry in entries:
        headers = {
            h["name"].lower(): h["value"]
            for h in entry["request"].get("headers", [])
        }
        if "authorization" in headers:
            auth_header_count += 1
        if "cookie" in headers:
            cookie_count += 1

        # Detect custom auth headers
        for name in headers:
            if name in IGNORE_HEADERS:
                continue
            if AUTH_HEADER_PATTERNS.search(name):
                custom_header_counts[name] = custom_header_counts.get(name, 0) + 1

        post_data = _parse_post_data(entry["request"].get("postData"))
        if isinstance(post_data, dict):
            if any(k.lower() in ("token", "access_token", "accesstoken") for k in post_data):
                token_in_body_count += 1

    if auth_header_count >= cookie_count and auth_header_count > 0:
        return {"auth_type": "bearer", "header": "Authorization"}
    if cookie_count > 0:
        return {"auth_type": "cookie", "header": "Cookie"}

    # Check for custom auth headers (e.g., x-mcco-sso-token, clientsecret)
    if custom_header_counts:
        # Pick the most common token/secret headers
        sorted_headers = sorted(custom_header_counts.items(), key=lambda x: -x[1])
        auth_headers = [h for h, _ in sorted_headers]
        return {
            "auth_type": "custom_headers",
            "headers": auth_headers,
        }

    if token_in_body_count > 0:
        return {"auth_type": "token_in_body"}
    return {"auth_type": "unknown"}


def _detect_envelope(responses: list[Any]) -> dict:
    """Detect the response envelope pattern from multiple API responses."""
    if not responses:
        return {}

    # Try common envelope patterns
    patterns = [
        # {resultCode: "OK", data: T}
        {"success_field": "resultCode", "success_values": ["OK", "ok", "Ok"], "data_fields": ["data", "result"]},
        # {code: 0, data: T}
        {"success_field": "code", "success_values": [0, "0", 200, "200"], "data_fields": ["data", "result"]},
        # {status: {code: 0}, body: T}
        {"success_field": "status.code", "success_values": [0, "0"], "data_fields": ["body"]},
        # {errcode: 0, data: T}
        {"success_field": "errcode", "success_values": [0, "0"], "data_fields": ["data", "result"]},
        # {success: true, data: T}
        {"success_field": "success", "success_values": [True, "true"], "data_fields": ["data", "result"]},
        # {ret: 0, data: T}
        {"success_field": "ret", "success_values": [0, "0"], "data_fields": ["data"]},
    ]

    json_responses = [r for r in responses if isinstance(r, dict)]
    if not json_responses:
        return {}

    for pattern in patterns:
        sf = pattern["success_field"]
        match_count = 0
        data_field_found = None

        for resp in json_responses:
            # Navigate nested fields
            val = resp
            for key in sf.split("."):
                if isinstance(val, dict):
                    val = val.get(key)
                else:
                    val = None
                    break

            if val in pattern["success_values"]:
                match_count += 1
                for df in pattern["data_fields"]:
                    if df in resp:
                        data_field_found = df
                    elif "." in sf:
                        # For nested success field, data might be at root
                        top_key = sf.split(".")[0]
                        parent = resp.get(top_key)
                        if isinstance(parent, dict):
                            # data is sibling to the success field's parent
                            pass
                        if df in resp:
                            data_field_found = df

        if match_count >= min(len(json_responses) * 0.3, 10) and match_count > 0:
            success_value = pattern["success_values"][0]
            # Resolve the actual success value from responses
            for resp in json_responses:
                val = resp
                for key in sf.split("."):
                    if isinstance(val, dict):
                        val = val.get(key)
                    else:
                        val = None
                        break
                if val in pattern["success_values"]:
                    success_value = val
                    break

            result = {"success_field": sf, "success_value": success_value}
            if data_field_found:
                result["data_field"] = data_field_found
            return result

    return {}


def _detect_base_url(urls: list[str]) -> str:
    """Find the common API base URL from a list of URLs."""
    if not urls:
        return ""

    parsed = [urlparse(u) for u in urls]

    # Group by scheme+netloc
    netloc_counter = Counter(f"{p.scheme}://{p.netloc}" for p in parsed)
    base, _ = netloc_counter.most_common(1)[0]

    # Try to find a common path prefix
    paths = [p.path for p in parsed if f"{p.scheme}://{p.netloc}" == base]
    if not paths:
        return base

    # Find common path prefix
    prefix_parts = paths[0].strip("/").split("/")
    common_depth = 0
    for i, part in enumerate(prefix_parts):
        if all(
            p.strip("/").split("/")[i] == part
            for p in paths
            if len(p.strip("/").split("/")) > i
        ):
            # Only count if it looks like a prefix (e.g., "api", "v1", "v2")
            if re.match(r"^(api|v\d+|rest|graphql|rpc)$", part, re.IGNORECASE):
                common_depth = i + 1
            elif i == 0 and common_depth == 0:
                common_depth = 1
        else:
            break

    if common_depth > 0:
        prefix = "/" + "/".join(prefix_parts[:common_depth])
        return base + prefix

    return base


# Patterns that indicate a dynamic path segment (ID, UUID, hash, etc.)
_DYNAMIC_SEGMENT = re.compile(
    r"^("
    r"[0-9a-f]{8,}"          # hex hash (8+ chars)
    r"|[0-9a-f]{8}-[0-9a-f]{4}-"  # UUID prefix
    r"|\d{6,}"                # long numeric ID
    r")$",
    re.IGNORECASE,
)


def _parameterize_urls(endpoints: list[dict]) -> list[dict]:
    """Replace dynamic path segments with {id} and deduplicate."""
    seen: dict[str, dict] = {}  # key -> first endpoint with that pattern
    for ep in endpoints:
        parsed = urlparse(ep["url"])
        parts = parsed.path.strip("/").split("/")
        normalized = []
        for part in parts:
            if _DYNAMIC_SEGMENT.match(part):
                normalized.append("{id}")
            else:
                normalized.append(part)
        norm_path = "/" + "/".join(normalized)
        key = f"{ep['method']}:{norm_path}"
        if key not in seen:
            ep_copy = dict(ep)
            ep_copy["url"] = f"{parsed.scheme}://{parsed.netloc}{norm_path}"
            seen[key] = ep_copy
    return list(seen.values())


def _group_by_domain(endpoints: list[dict], base_url: str) -> list[dict]:
    """Group endpoints by URL path prefix into logical domains."""
    base_path = urlparse(base_url).path.rstrip("/")

    # Collect path segments after base
    domain_endpoints: dict[str, list[dict]] = defaultdict(list)

    for ep in endpoints:
        parsed = urlparse(ep["url"])
        rel_path = parsed.path
        if base_path and rel_path.startswith(base_path):
            rel_path = rel_path[len(base_path):]

        parts = [p for p in rel_path.strip("/").split("/") if p]
        if parts:
            domain_name = parts[0]
        else:
            domain_name = "root"

        ep_copy = dict(ep)
        ep_copy["path"] = parsed.path
        del ep_copy["url"]
        domain_endpoints[domain_name].append(ep_copy)

    domains = []
    for name, eps in sorted(domain_endpoints.items()):
        # Find the common prefix for this domain
        paths = [ep["path"] for ep in eps]
        if paths:
            # Use the shortest common prefix
            prefix_parts = paths[0].strip("/").split("/")
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
        else:
            prefix = f"/{name}"

        domains.append({
            "name": name,
            "prefix": prefix,
            "endpoints": eps,
        })

    return domains


def parse_har(
    har_path: str,
    site_name: str | None = None,
) -> dict:
    """Parse a HAR file and produce an API catalog.

    Args:
        har_path: Path to the .har file.
        site_name: Override for the site name (default: derived from the most common hostname).

    Returns:
        Structured API catalog dict.
    """
    har = load_har(har_path)
    entries = har["log"]["entries"]

    # Filter to API entries
    api_entries = [e for e in entries if _is_api_response(e)]

    if not api_entries:
        print("Warning: No API entries found in HAR file.", file=sys.stderr)
        return {"site_name": site_name or "unknown", "base_url": "", "domains": []}

    # Extract endpoints
    endpoints = []
    response_bodies = []
    urls = []

    for entry in api_entries:
        req = entry["request"]
        resp = entry["response"]

        url = req["url"]
        urls.append(url)

        request_body = _parse_post_data(req.get("postData"))
        response_body = _parse_response_body(resp.get("content", {}))
        response_bodies.append(response_body)

        endpoints.append({
            "method": req["method"],
            "url": url,
            "request_example": request_body,
            "response_example": response_body,
        })

    # Detect site name from most common hostname
    if not site_name:
        hostnames = [urlparse(u).netloc for u in urls]
        most_common_host = Counter(hostnames).most_common(1)[0][0]
        # Extract meaningful name: "api.meican.com" -> "meican"
        parts = most_common_host.split(".")
        site_name = parts[-2] if len(parts) >= 2 else parts[0]

    # Detect base URL
    base_url = _detect_base_url(urls)

    # Detect auth
    auth_info = _extract_auth_info(api_entries)
    env_prefix = site_name.upper().replace("-", "_")
    if auth_info["auth_type"] == "cookie":
        auth_info["env_var"] = f"{env_prefix}_COOKIE"
    elif auth_info["auth_type"] == "custom_headers":
        auth_info["env_var"] = f"{env_prefix}_TOKEN"
    else:
        auth_info["env_var"] = f"{env_prefix}_TOKEN"

    # Detect response envelope
    envelope = _detect_envelope(response_bodies)

    # Parameterize dynamic URL segments and deduplicate
    endpoints = _parameterize_urls(endpoints)

    # Group by domain
    domains = _group_by_domain(endpoints, base_url)

    return {
        "site_name": site_name,
        "base_url": base_url,
        "auth_type": auth_info["auth_type"],
        "auth_details": auth_info,
        "response_envelope": envelope,
        "domains": domains,
    }


def main():
    parser = argparse.ArgumentParser(description="Parse HAR file to API catalog JSON")
    parser.add_argument("har_file", help="Path to .har file")
    parser.add_argument("--output", "-o", help="Output JSON file (default: stdout)")
    parser.add_argument("--site-name", "-n", help="Override site name")
    args = parser.parse_args()

    catalog = parse_har(args.har_file, site_name=args.site_name)

    output = json.dumps(catalog, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"API catalog written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""JS bundle API endpoint extractor.

Supplementary tool that extracts API endpoints from JavaScript bundles
by analyzing fetch/axios calls, route definitions, and API path constants.

Usage:
    python js_analyzer.py <url-or-html-file> [--output <json-file>]
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


def extract_script_urls(html: str, base_url: str) -> list[str]:
    """Extract <script src="..."> URLs from HTML."""
    pattern = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
    urls = []
    for match in pattern.finditer(html):
        src = match.group(1)
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(base_url, src)
        elif not src.startswith("http"):
            src = urljoin(base_url, src)
        urls.append(src)
    return urls


def extract_api_paths(js_content: str) -> list[dict]:
    """Extract API endpoint patterns from JS code."""
    endpoints: list[dict] = []
    seen = set()

    patterns = [
        # fetch("/api/...")
        (r'fetch\s*\(\s*["\']([^"\']*\/api\/[^"\']+)["\']', "fetch"),
        # axios.get/post/put/delete("/api/...")
        (r'axios\s*\.\s*(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', "axios"),
        # Generic HTTP method + URL pattern
        (r'\.\s*(get|post|put|delete|patch)\s*\(\s*["\'](\/?api\/[^"\']+)["\']', "method"),
        # String constants with /api/ paths
        (r'["\'](\/?api\/[a-zA-Z0-9/_-]+)["\']', "string"),
        # GraphQL endpoint
        (r'["\'](\/?graphql)["\']', "graphql"),
        # URL template literals with /api/
        (r'`([^`]*\/api\/[^`]*)`', "template"),
    ]

    for pattern, source in patterns:
        for match in re.finditer(pattern, js_content):
            if source == "axios":
                method = match.group(1).upper()
                path = match.group(2)
            elif source == "method":
                method = match.group(1).upper()
                path = match.group(2)
            elif source == "fetch":
                method = "GET"  # Default; actual method may be in options
                path = match.group(1)
            else:
                method = "UNKNOWN"
                path = match.group(1)

            # Clean up template literals
            path = re.sub(r'\$\{[^}]+\}', '{param}', path)

            # Normalize
            if not path.startswith("/"):
                path = "/" + path

            key = f"{method}:{path}"
            if key not in seen:
                seen.add(key)
                endpoints.append({
                    "method": method,
                    "path": path,
                    "source": source,
                })

    return endpoints


def fetch_url(url: str) -> str:
    """Fetch URL content."""
    if requests is None:
        raise RuntimeError("requests library required: pip install requests")
    resp = requests.get(url, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })
    resp.raise_for_status()
    return resp.text


def analyze_website(url: str, max_bundles: int = 10) -> dict[str, Any]:
    """Analyze a website's JS bundles for API endpoints.

    Args:
        url: Website URL to analyze.
        max_bundles: Maximum number of JS bundles to download and analyze.

    Returns:
        Dict with discovered endpoints.
    """
    print(f"Fetching {url}...", file=sys.stderr)
    html = fetch_url(url)

    script_urls = extract_script_urls(html, url)
    print(f"Found {len(script_urls)} script tags", file=sys.stderr)

    # Filter to likely app bundles (skip vendor/lib scripts)
    app_scripts = []
    vendor_patterns = re.compile(
        r"(vendor|polyfill|chunk-vendors|runtime|jquery|lodash|react\.production|vue\.runtime)",
        re.IGNORECASE,
    )
    for s in script_urls:
        if not vendor_patterns.search(s):
            app_scripts.append(s)

    # If too few app scripts, include all
    if len(app_scripts) < 2:
        app_scripts = script_urls

    all_endpoints: list[dict] = []
    analyzed_count = 0

    for script_url in app_scripts[:max_bundles]:
        try:
            print(f"  Analyzing {script_url}...", file=sys.stderr)
            js = fetch_url(script_url)
            endpoints = extract_api_paths(js)
            for ep in endpoints:
                ep["source_bundle"] = script_url
            all_endpoints.extend(endpoints)
            analyzed_count += 1
        except Exception as e:
            print(f"  Failed to fetch {script_url}: {e}", file=sys.stderr)

    # Deduplicate
    seen = set()
    unique = []
    for ep in all_endpoints:
        key = f"{ep['method']}:{ep['path']}"
        if key not in seen:
            seen.add(key)
            unique.append(ep)

    return {
        "url": url,
        "scripts_found": len(script_urls),
        "scripts_analyzed": analyzed_count,
        "endpoints": unique,
    }


def analyze_local_html(html_path: str) -> dict[str, Any]:
    """Analyze a local HTML file for API endpoints."""
    html = Path(html_path).read_text(encoding="utf-8")

    # Extract inline scripts
    inline_pattern = re.compile(r'<script[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
    all_endpoints: list[dict] = []

    for match in inline_pattern.finditer(html):
        js = match.group(1).strip()
        if js:
            endpoints = extract_api_paths(js)
            for ep in endpoints:
                ep["source_bundle"] = "inline"
            all_endpoints.extend(endpoints)

    # Deduplicate
    seen = set()
    unique = []
    for ep in all_endpoints:
        key = f"{ep['method']}:{ep['path']}"
        if key not in seen:
            seen.add(key)
            unique.append(ep)

    return {
        "source": html_path,
        "endpoints": unique,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract API endpoints from website JS bundles")
    parser.add_argument("target", help="Website URL or local HTML file path")
    parser.add_argument("--output", "-o", help="Output JSON file (default: stdout)")
    parser.add_argument("--max-bundles", type=int, default=10, help="Max JS bundles to analyze")
    args = parser.parse_args()

    if args.target.startswith("http://") or args.target.startswith("https://"):
        result = analyze_website(args.target, max_bundles=args.max_bundles)
    else:
        result = analyze_local_html(args.target)

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Results written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

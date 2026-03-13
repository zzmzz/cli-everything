# cli-everything

> **Acknowledgment**: This project is inspired by and builds upon [cli-anything](https://github.com/anthropics/cli-anything) by Anthropic, which generates CLI interfaces for open-source GUI applications. cli-everything extends the same philosophy to closed-source websites. Generated packages share the `cli_anything.*` namespace convention via PEP 420.

A Claude Code plugin that reverse-engineers closed-source website APIs and generates CLI harnesses.

While **cli-anything** builds CLI interfaces for open-source applications by reading source code, **cli-everything** handles closed-source websites by:

- **Chrome CDP Recording** — Launch Chrome, let the user operate manually, passively record all network requests and user interactions via Chrome DevTools Protocol
- **JS Bundle Analysis** — Search loaded JS bundles for API patterns, auth config, parameter construction
- **Parameter Provenance Tracing** — Automatically trace where each request parameter came from (earlier responses, cookies, constants)

The generated CLI packages are installable via pip, using Click commands, with `--json` output support.

## Quick Start

```bash
# 1. Install dependencies
pip install websocket-client

# 2. In Claude Code, run the plugin command
/cli-everything https://www.meican.com/ --tasks "login, view menu, place order"
```

This will:
1. Launch Chrome with the target URL
2. Ask you to perform each task in the browser
3. Passively record all network requests and interactions
4. Analyze the captured data
5. Generate a complete CLI package

## Example: Meican (美餐)

A fully working CLI for [meican.com](https://www.meican.com/) (corporate meal ordering) is included under `meican/agent-harness/`:

```bash
cd meican/agent-harness
pip install -e .

# Login
cli-anything-meican login --email you@company.com --password xxx

# View meal calendar
cli-anything-meican calendar list --date 2026-03-19

# List restaurants for a meal tab
cli-anything-meican restaurant list --tab-id <tab-uuid> --time "2026-03-19 09:30"

# View restaurant menu
cli-anything-meican restaurant menu --tab-id <tab-uuid> --time "2026-03-19 09:30" --restaurant-id <id>

# Place an order
cli-anything-meican order place --tab-id <tab-uuid> --time "2026-03-19 09:30" --dish-id <id>

# Cancel an order
cli-anything-meican order cancel <order-uuid>
```

All commands support `--json` for machine-readable output.

## How It Works

1. **Chrome Recording** — Opens Chrome with CDP; the user operates manually; all XHR/Fetch requests and user clicks/inputs are passively captured
2. **Timeline Analysis** — Correlates user actions with the API calls they triggered
3. **Deep Analysis** — Inspects cookies/localStorage, traces parameter provenance, searches JS bundles
4. **Catalog Generation** — Groups endpoints by domain, detects auth and response formats
5. **Code Generation** — Produces a complete Python/Click CLI package
6. **Testing & Installation** — Generates tests, verifies `pip install -e .`

## Generated Output Structure

```
<site>/agent-harness/
├── setup.py
└── cli_anything/          # PEP 420 namespace (no __init__.py)
    └── <site>/
        ├── __init__.py
        ├── __main__.py
        ├── <site>_cli.py  # Click CLI entry point
        ├── core/          # One module per API domain
        ├── utils/         # http_client.py + output.py
        └── tests/
```

## Project Structure

```
cli-everything/
├── .claude-plugin/          # Claude Code plugin definition
├── HARNESS_WEB.md           # Web reverse-engineering SOP
├── commands/
│   └── cli-everything.md    # /cli-everything command prompt
├── scripts/
│   ├── chrome_recorder.py   # Chrome CDP passive recorder
│   ├── browser_server.py    # Browser automation server
│   ├── browser_client.py    # Browser automation client
│   ├── har_parser.py        # HAR file → API catalog JSON
│   └── js_analyzer.py       # JS bundle API extractor
├── templates/               # Code generation templates
│   ├── setup.py.tpl
│   ├── http_client.py.tpl
│   ├── cli_entry.py.tpl
│   ├── core_module.py.tpl
│   ├── output.py.tpl
│   ├── test_core.py.tpl
│   └── test_e2e.py.tpl
└── meican/                  # Example: generated meican CLI
    └── agent-harness/
```

## Scripts

### chrome_recorder.py

```bash
# Launch Chrome and start recording
python scripts/chrome_recorder.py --url https://example.com/ --port 8766

# Connect to already-running Chrome
python scripts/chrome_recorder.py --url https://example.com/ --no-launch --debug-port 9222

# Reuse existing Chrome profile (preserves login cookies)
python scripts/chrome_recorder.py --url https://example.com/ --user-data-dir ~/chrome-profile
```

## cli-anything vs cli-everything

| Aspect | cli-anything | cli-everything |
|--------|-------------|----------------|
| Target | Open-source GUI apps | Closed-source websites |
| Input | Source code / GitHub URL | Website URL (user operates Chrome) |
| Analysis | AST, grep, source reading | CDP recording, JS analysis, param tracing |
| Backend | Real software CLI | HTTP API (requests) |
| Output | Same `cli_anything.*` namespace packages |

📖 [中文文档](README_CN.md)

## License

MIT

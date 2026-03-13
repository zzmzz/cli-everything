# cli-everything Command

Analyze a closed-source website's API by directly browsing and interacting with it, then generate a complete CLI harness.

## CRITICAL: Read HARNESS_WEB.md First

**Before doing anything else, you MUST read `./HARNESS_WEB.md`.** It defines the complete methodology, the guided workflow, practical tips, and a real-world example (meican.com). Follow it step by step.

## Usage

```bash
/cli-everything <website-url> [--tasks "login, list orders, place order"] [--output <dir>]
```

## Arguments

- `<website-url>` - **Required.** The website URL to analyze (e.g., `https://www.meican.com/`)
- `--tasks <task-list>` - **Recommended.** Comma-separated list of user operations to capture (e.g., "login, list orders, view menu")
- `--output <dir>` - Output directory for the generated CLI package (default: `./<site-name>/`)

## Workflow Summary

The full guided workflow is defined in `HARNESS_WEB.md`. Here is a condensed overview:

### Phase 0: Launch Chrome Recorder

```bash
.venv/bin/python scripts/chrome_recorder.py --url <website-url> --port 8766 &
# Wait for ready
curl -s http://localhost:8766/health
```

Chrome opens with the target URL. **The user operates the browser manually.**
The recorder passively captures all network requests and user interactions via CDP.

### Phase 1: User-Driven Task Capture

Tell the user what tasks to perform. For **each** task:

```
1. curl /requests/clear && curl /actions/clear    ← isolate this task
2. Tell the user what to do (e.g., "请登录你的账号")
3. Wait for user to finish operating the browser
4. curl /requests/summary                         ← see what APIs were triggered
5. curl /actions/summary                          ← see what the user clicked/typed
6. curl /requests                                 ← get full request/response details
7. curl /timeline                                 ← chronological view of actions + requests
```

The recorder automatically captures:
- All XHR/Fetch network requests with headers, body, response
- User clicks, form inputs, form submissions, keyboard events
- Page navigations
- Request initiator info (which JS triggered each request)

### Phase 2: Deep Analysis

After the user has completed all tasks:

```bash
curl -s http://localhost:8766/storage          # cookies, localStorage, sessionStorage
curl -s http://localhost:8766/trace-params      # automatic parameter provenance tracing
curl -s -X POST http://localhost:8766/search-js -d '{"pattern":"/api/"}'     # search JS bundles
curl -s http://localhost:8766/analyze-js        # global JS state and API configs
```

Key questions to answer:
- **Auth**: How does login work? Where are tokens stored? How are they refreshed?
- **Parameters**: Which request params come from earlier API responses? Which are constants?
- **Envelope**: What response format(s) does the site use? Are there multiple?
- **Missing APIs**: Are there endpoints in JS that weren't triggered during browsing?

### Phase 3: Export & Review Catalog

```bash
curl -s -X POST http://localhost:8766/export-catalog -d '{"site_name":"<site>"}'
```

Enrich with analysis results, present to user for confirmation.

### Phase 4-6: Design → Generate → Test → Install

Follow HARNESS_WEB.md Phase 4-7. Generate code from templates, run tests, install.

### Phase 7: Cleanup

```bash
curl -s -X POST http://localhost:8766/close
```

## Chrome Recorder API Reference

### Status & Screenshots

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| GET | /health | - | Server status, request/action counts |
| GET | /screenshot | - | Take screenshot via CDP, returns `{path}` |
| GET | /page-info | - | Current URL and title |
| GET | /elements | ?selector=CSS | List interactive elements |
| POST | /evaluate | `{expression}` | Run JavaScript via CDP |
| POST | /close | - | Stop Chrome and server |

### Network Capture (passive, auto-recorded)

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| GET | /requests | ?since=timestamp | All captured XHR/Fetch with full details |
| GET | /requests/summary | - | Brief: method, url, status |
| GET | /requests/clear | - | Clear captures (do before each task) |
| POST | /export-catalog | `{site_name?}` | Generate structured API catalog JSON |

### User Action Recording (passive, auto-recorded)

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| GET | /actions | ?since=timestamp | All recorded user actions (clicks, inputs, etc.) |
| GET | /actions/summary | - | Brief action list |
| GET | /actions/clear | - | Clear action log |
| POST | /timeline | - | Chronological combined view of actions + requests |

### Deep Analysis

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| GET | /storage | - | Dump cookies, localStorage, sessionStorage |
| GET | /trace-params | - | Trace where each request parameter came from |
| GET | /analyze-js | - | Analyze global JS state, API configs |
| POST | /search-js | `{pattern, context_chars?}` | Search all JS bundles for a string |
| GET | /navigations | - | All page navigation events |
| GET | /console | ?limit=50 | Console messages |
| POST | /reinject-observer | - | Re-inject action observer (after SPA navigation) |

## Output Structure

```
<site-name>/
└── agent-harness/
    ├── <SITE>.md
    ├── setup.py
    └── cli_anything/                  # NO __init__.py (PEP 420 namespace)
        └── <site>/
            ├── __init__.py
            ├── __main__.py
            ├── README.md
            ├── <site>_cli.py
            ├── core/
            │   ├── __init__.py
            │   └── <domain>.py
            ├── utils/
            │   ├── __init__.py
            │   ├── http_client.py
            │   └── output.py
            └── tests/
                ├── __init__.py
                ├── TEST.md
                ├── test_core.py
                └── test_full_e2e.py
```

## Example

```bash
# Basic usage — agent will ask what tasks to capture
/cli-everything https://www.meican.com/

# With tasks specified upfront
/cli-everything https://www.meican.com/ --tasks "login, view calendar, list restaurants, view menu, place order, check order status"

# Custom output directory
/cli-everything https://www.meican.com/ --output ~/projects/meican
```

## Success Criteria

1. Browser successfully opened and navigated the target website
2. All user-specified tasks were performed and their API calls captured
3. Deep analysis completed: storage inspected, params traced, JS searched
4. API catalog is generated and confirmed by the user
5. Auth flow fully documented (login → token → refresh)
6. Parameter provenance documented (which params come from where)
7. All core modules implemented with correct API calls
8. CLI supports `--json` output mode
9. All tests pass (100% pass rate)
10. `pip install -e .` succeeds and `cli-anything-<site> --help` works

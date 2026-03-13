# Agent Harness: Web API Reverse-Engineering for Closed-Source Websites

## Purpose

This harness provides a standard operating procedure (SOP) for coding agents to build
CLI interfaces for **closed-source websites** where source code is unavailable. The agent
launches Chrome with CDP (Chrome DevTools Protocol) recording, and the **user operates the
browser manually**. The recorder passively captures all network requests, user interactions,
page JS, and navigation events. The agent then analyzes this data and generates a
production-ready CLI harness.

The generated CLIs follow the `cli_anything.*` namespace convention — installable via pip,
with Click commands, `--json` output, and REPL mode.

---

## Complete Guided Workflow

### Phase 0: Launch Chrome Recorder

#### 0.1 Start the Chrome recorder

```bash
.venv/bin/python scripts/chrome_recorder.py --url <website-url> --port 8766 &
```

This launches Chrome with remote debugging and connects via CDP. The user sees a normal
Chrome window and can operate it freely.

Options:
- `--debug-port 9222` — Chrome remote debugging port (default 9222)
- `--no-launch` — Connect to an already running Chrome instance (must have `--remote-debugging-port`)
- `--user-data-dir <path>` — Reuse an existing Chrome profile (with cookies, etc.)

Wait for ready:
```bash
curl -s http://localhost:8766/health
# → {"status": "ok", "connected": true, "captured_requests": 0, "user_actions": 0}
```

#### 0.2 Verify the page

```bash
curl -s http://localhost:8766/screenshot
# → {"path": "/tmp/cli-everything/screenshots/screenshot_0001.png"}
# READ the image to confirm the page loaded correctly.

curl -s http://localhost:8766/page-info
# → {"url": "...", "title": "..."}
```

Recording is automatic — all network requests and user interactions are captured from the start.

---

### Phase 1: User-Driven Task Capture

The user operates the browser manually. The agent guides them and monitors the recordings.

For **each** user task (login, browse menu, place order, etc.):

#### Step 1: Clear previous captures

```bash
curl -s http://localhost:8766/requests/clear
curl -s http://localhost:8766/actions/clear
```

#### Step 2: Ask the user to perform the task

Tell the user what to do in the browser, e.g.:
- "请登录你的账号"
- "请浏览菜单并选一个菜品"
- "请下一个订单"

The user operates Chrome normally. All their clicks, inputs, form submissions,
and the resulting network requests are automatically recorded.

#### Step 3: Review what happened

```bash
# What the user did (clicks, inputs, form submits)
curl -s http://localhost:8766/actions/summary

# What API calls were triggered
curl -s http://localhost:8766/requests/summary

# Full request/response details
curl -s http://localhost:8766/requests

# Combined chronological timeline (actions + requests interleaved)
curl -s -X POST http://localhost:8766/timeline
```

The timeline view is especially useful — it shows the causal relationship between
user actions and the API calls they trigger.

#### Step 4: Screenshot to see current state

```bash
curl -s http://localhost:8766/screenshot
# READ the image to see where the user ended up
```

#### Step 5: Record the mapping

For each task, note:
- Which user action(s) triggered which API endpoint(s)
- The HTTP method, URL pattern, and key parameters
- The response structure
- Which parameters are user-supplied vs derived from earlier responses
- The initiator info (which JS function built the request)

---

### Phase 2: Deep Analysis

After capturing all tasks, run the analysis tools to understand the full picture.

#### 2.1 Inspect browser storage

```bash
curl -s http://localhost:8766/storage
```

Returns `cookies`, `localStorage`, `sessionStorage`. This reveals:
- Where auth tokens are stored (e.g., `MC_TOKEN` in localStorage)
- Session identifiers, client IDs, feature flags
- What cookies the server sets after login

#### 2.2 Trace parameter provenance

```bash
curl -s http://localhost:8766/trace-params
```

Automatically analyzes the entire captured request chain and reports:
- For each request parameter, which earlier response it came from
- Cross-request data flow (e.g., login response → order request)
- Shared constants like `client_id` / `client_secret`

**Example output:**
```
POST /api/v2/payment-slips/pay
  body.paymentSlipId = "142407796070091843"
    ← from request[0] (POST /orders/add)  response.order.paymentSlipId
  header.x-mcco-signature = "uOBNV5Pm..."
    ← from request[0] (POST /orders/add)  response.order.signature
```

This is critical for understanding multi-step flows like:
- Login → get ticket → choose account → get access token
- Add to cart → submit order → get payment slip → pay

#### 2.3 Search JS bundles for patterns

```bash
curl -s -X POST http://localhost:8766/search-js \
  -d '{"pattern":"/api/","context_chars":200}'
```

Searches all inline and external JS files loaded by the page. Use this to discover:
- API base URL configuration (e.g., `"production":"https://example.com/forward/api"`)
- GraphQL query/mutation definitions
- API endpoints defined but not yet triggered during browsing
- Parameter construction logic

**Useful search patterns:**
```bash
# Find API base URL config
/search-js  {"pattern":"forward/"}
/search-js  {"pattern":"baseURL"}
/search-js  {"pattern":"API_ENV"}

# Find GraphQL operations
/search-js  {"pattern":"mutation "}
/search-js  {"pattern":"query "}

# Find specific API paths you saw in requests
/search-js  {"pattern":"orders/add"}

# Find auth/token handling
/search-js  {"pattern":"accessToken"}
/search-js  {"pattern":"refreshToken"}
```

#### 2.4 Analyze JS global state

```bash
curl -s http://localhost:8766/analyze-js
```

Inspects the live browser for:
- Global API config objects (`API_BASE`, `baseURL`, `config`, etc.)
- Axios defaults and interceptors
- Inline script API patterns

#### 2.5 Review user action log

```bash
curl -s http://localhost:8766/actions
```

Returns all recorded user interactions with element details, helping correlate
which UI elements trigger which API calls.

#### 2.6 Check page navigations

```bash
curl -s http://localhost:8766/navigations
```

Shows all page navigation events, useful for understanding SPA routing.

---

### Phase 3: Build the API Catalog

#### 3.1 Export the auto-generated catalog

```bash
curl -s -X POST http://localhost:8766/export-catalog \
  -d '{"site_name":"<site>"}'
```

This produces a structured JSON catalog from all captured requests, including:
- Auto-detected auth type (bearer, cookie, custom headers)
- Response envelope pattern
- Endpoints grouped by domain
- Request/response examples

#### 3.2 Enrich with analysis results

Manually refine the catalog using insights from Phase 2:

1. **Auth flow**: Document the full chain (e.g., GraphQL login → token → bearer header)
2. **Parameter sources**: Mark which params come from prior API responses vs user input vs constants
3. **Multiple response envelopes**: Some sites use different envelopes for different API versions
4. **Hidden APIs**: Add endpoints found via JS search but not triggered during browsing

#### 3.3 Present to user for confirmation

Show the user:
- Complete list of discovered endpoints grouped by domain
- Auth method and where tokens come from
- Proposed CLI command names and parameter mappings
- Any gaps or missing operations

---

### Phase 4: CLI Architecture Design

1. **Map domains to Click command groups** — each API domain becomes a group
2. **Map endpoints to subcommands** — each endpoint becomes a subcommand
3. **Design global options**:
   - `--base-url` (with env var fallback)
   - `--token` / `--cookie` (auth, with env var fallback)
   - `--json` (machine-readable output)
   - Site-specific globals (e.g., `--tab-id`, `--corp-id`)
4. **Design per-command options** from request parameters
5. **Create `<SITE>.md`** — site-specific SOP document

### Phase 5: Code Generation

Generate a complete Python package using templates from `templates/`:

1. **`setup.py`** — namespace package configuration
2. **`http_client.py`** — adapted for the site's auth and response envelope
3. **`<site>_cli.py`** — Click entry point with all command groups
4. **`core/<domain>.py`** — one module per API domain
5. **`utils/output.py`** — output formatting
6. **`__init__.py`, `__main__.py`** — package boilerplate

Templates use Jinja2-style `{{ variable }}` placeholders. Read each template as a reference
and generate the final Python code directly, adapting to the specific site.

### Phase 6: Testing

1. **Create `TEST.md`** with test plan
2. **Write `test_core.py`** — unit tests for core modules (mock HTTP)
3. **Write `test_full_e2e.py`** — E2E tests using `_resolve_cli()` for subprocess tests
4. **Run tests** and document results in TEST.md

### Phase 7: Installation & Verification

1. `pip install -e .` in the `agent-harness/` directory
2. Verify `cli-anything-<site> --help` works
3. Verify a real API call with valid credentials
4. Run `pytest -v -s` and confirm all tests pass

### Phase 8: Cleanup

```bash
curl -s -X POST http://localhost:8766/close
```

---

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

---

## Real-World Example: meican.com

This is a complete walkthrough of reverse-engineering meican.com (corporate meal ordering).

### Discovery Results

**API Architecture:**
- REST API: `https://www.meican.com/forward/api/v2.1/...` and `v3.0/...`
- GraphQL: `https://gateway.meican.com/graphql?op=<OperationName>`
- Payment API: `https://meican-pay-checkout-bff.meican.com/api/v2/...`

**Auth Flow (3-step):**
1. `POST gateway.meican.com/graphql?op=LoginByAuthWay` — email + password → returns `ticket`, `signature`, `snowflakeId`
2. `POST gateway.meican.com/graphql?op=ChooseAccountLogin` — ticket + snowflakeId + signature → returns `accessToken`, `refreshToken`
3. All subsequent REST requests carry:
   - Header: `authorization: bearer <accessToken>`
   - Header: `clientid: <fixed_client_id>` (app constant, found via JS search)
   - Header: `clientsecret: <fixed_client_secret>` (app constant)
   - URL params: `client_id=...&client_secret=...` (same values)
4. Token stored in: cookie `sat` + localStorage `MC_TOKEN`
5. Token refresh: `POST /forward/api/v2.1/oauth/token` with `grant_type=refresh_token`

**Response Envelopes (multiple!):**
- Most v2.1/v3.0: `{resultCode: "OK", resultDescription: "...", data: T}`
- Gateway APIs: `{code: 0, msg: "...", data: T}`
- Some endpoints: `{success: true, data: T, message: "..."}`
- Calendar: no envelope, direct data `{startDate, endDate, dateList}`

**Task → API Mapping:**

| User Task | API Endpoint | Key Parameters |
|-----------|-------------|----------------|
| Login (email) | GraphQL `LoginByAuthWay` → `ChooseAccountLogin` | email, password |
| View calendar | `GET /calendarItems/list` | - |
| List restaurants | `GET /restaurants/list` | tabUniqueId, targetTime |
| View menu | `GET /restaurants/show` | tabUniqueId, targetTime, restaurantUniqueId |
| Add to cart | `POST /preorder/cart/update` | tabUniqueId/time key, dishes array |
| Place order | `POST /orders/add` | tabUniqueId, dishId, count, targetTime, addressUniqueId |
| Pay order | `POST /payment-slips/pay` | paymentSlipId (← from orders/add response) |
| Check order | `GET /gateway/group-meals/v1/order/{id}` | order uniqueId (← from orders/add response) |
| Cancel order | Via UI "取消订单" button | Captures cancel API |
| Token refresh | `POST /oauth/token` | refresh_token |

**Parameter Provenance (traced automatically):**
- `orders/add` → response contains `uniqueId`, `paymentSlipId`, `signature`, `timestamp`, `mchId`, `nonceStr`
- `payment-slips/pay` → uses `paymentSlipId` from above + signature headers from above
- `restaurants/show` → uses `tabUniqueId` from `calendarItems/list` + `restaurantUniqueId` from `restaurants/list`

---

## Output Structure

```
<target-dir>/
└── agent-harness/
    ├── <SITE>.md                      # Site-specific SOP
    ├── setup.py
    └── cli_anything/                  # NO __init__.py (PEP 420 namespace)
        └── <site>/
            ├── __init__.py
            ├── __main__.py
            ├── README.md
            ├── <site>_cli.py          # Click entry: --base-url, --token, --json, REPL
            ├── core/
            │   ├── __init__.py
            │   └── <domain>.py        # One module per API domain
            ├── utils/
            │   ├── __init__.py
            │   ├── http_client.py     # Auth + response unwrap adapted to site
            │   └── output.py          # Reusable output formatting
            └── tests/
                ├── __init__.py
                ├── TEST.md
                ├── test_core.py
                └── test_full_e2e.py
```

**Critical:** The `cli_anything/` directory must NOT contain an `__init__.py`.
This is a PEP 420 namespace package — multiple cli-anything packages coexist.

## Rules

- **User-driven browser recording is the primary API discovery method.** Launch Chrome, let the user operate, passively capture everything.
- **Never control the browser programmatically for task capture.** The user's manual operation is faster and more reliable.
- **Always run deep analysis** — storage, trace-params, search-js — to understand where parameters come from. Raw request capture alone is not enough.
- **Use /timeline to correlate user actions with API calls.** This shows the causal chain.
- **Screenshot after each task** to verify the page state.
- **Clear requests + actions before each task** to isolate which APIs belong to which task.
- **Every generated package MUST follow the `cli_anything.*` namespace.** No exceptions.
- **Every package MUST have a README.md** explaining auth setup and usage.
- **Every CLI MUST support `--json`** for machine-readable output.
- **Auth credentials MUST support env var fallback** — never hardcode credentials.
- **Response unwrapping MUST match the site's actual envelope format.**
- **Tests MUST include subprocess tests** using `_resolve_cli()`.
- **Generated code MUST be immediately installable** via `pip install -e .`.
- **Always stop the recorder** when done with API discovery.

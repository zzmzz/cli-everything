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

---

# cli-everything（中文文档）

> **致谢**：本项目受 Anthropic 的 [cli-anything](https://github.com/anthropics/cli-anything) 启发并在其基础上扩展。cli-anything 为开源 GUI 应用生成 CLI 接口，cli-everything 则将同样的理念延伸到闭源网站。生成的包共享 `cli_anything.*` 命名空间（PEP 420）。

一个 Claude Code 插件，通过逆向工程闭源网站 API 来自动生成 CLI 工具。

**cli-anything** 通过阅读源码为开源应用生成 CLI，而 **cli-everything** 通过以下方式处理闭源网站：

- **Chrome CDP 录制** — 启动 Chrome，用户手动操作，通过 Chrome DevTools Protocol 被动录制所有网络请求和用户交互
- **JS 包分析** — 搜索加载的 JS 包中的 API 模式、认证配置、参数构造逻辑
- **参数溯源** — 自动追踪每个请求参数的来源（之前的响应、cookies、常量）

生成的 CLI 包可通过 pip 安装，使用 Click 命令，支持 `--json` 输出。

## 快速开始

```bash
# 1. 安装依赖
pip install websocket-client

# 2. 在 Claude Code 中运行插件命令
/cli-everything https://www.meican.com/ --tasks "login, view menu, place order"
```

这将会：
1. 启动 Chrome 并打开目标网站
2. 提示你在浏览器中执行各项操作
3. 被动录制所有网络请求和交互
4. 分析录制数据
5. 生成完整的 CLI 包

## 示例：美餐 (meican.com)

项目包含了一个完整可用的 [美餐](https://www.meican.com/) CLI（企业订餐平台），位于 `meican/agent-harness/`：

```bash
cd meican/agent-harness
pip install -e .

# 登录
cli-anything-meican login --email you@company.com --password xxx

# 查看餐次日历
cli-anything-meican calendar list --date 2026-03-19

# 查看某餐次可选餐厅
cli-anything-meican restaurant list --tab-id <tab-uuid> --time "2026-03-19 09:30"

# 查看餐厅菜单
cli-anything-meican restaurant menu --tab-id <tab-uuid> --time "2026-03-19 09:30" --restaurant-id <id>

# 下单
cli-anything-meican order place --tab-id <tab-uuid> --time "2026-03-19 09:30" --dish-id <id>

# 取消订单
cli-anything-meican order cancel <order-uuid>
```

所有命令均支持 `--json` 参数获取机器可读的 JSON 输出。

## 工作原理

1. **Chrome 录制** — 通过 CDP 打开 Chrome，用户正常操作，所有 XHR/Fetch 请求和点击/输入操作被被动捕获
2. **时间线分析** — 将用户操作与其触发的 API 调用关联起来
3. **深度分析** — 检查 cookies/localStorage，追踪参数来源，搜索 JS 包
4. **接口编目** — 按域名分组端点，检测认证方式和响应格式
5. **代码生成** — 生成完整的 Python/Click CLI 包
6. **测试安装** — 生成测试用例，验证 `pip install -e .`

## License

MIT

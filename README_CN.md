# cli-everything

> **致谢**：本项目受 Anthropic 的 [cli-anything](https://github.com/anthropics/cli-anything) 启发并在其基础上扩展。cli-anything 为开源 GUI 应用生成 CLI 接口，cli-everything 则将同样的理念延伸到闭源网站。生成的包共享 `cli_anything.*` 命名空间（PEP 420）。

📖 [English](README.md)

一个 Claude Code 插件，通过逆向工程闭源网站 API 来自动生成 CLI 工具。

**cli-anything** 通过阅读源码为开源应用生成 CLI，而 **cli-everything** 通过以下方式处理闭源网站：

- **Chrome CDP 录制** — 启动 Chrome，用户手动操作，通过 Chrome DevTools Protocol 被动录制所有网络请求和用户交互
- **JS 包分析** — 搜索加载的 JS 包中的 API 模式、认证配置、参数构造逻辑
- **参数溯源** — 自动追踪每个请求参数的来源（之前的响应、cookies、常量）

生成的 CLI 包可通过 pip 安装，使用 Click 命令，支持 `--json` 输出。

## 安装

### 前置要求

- 已安装 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- Python 3.8+
- Google Chrome

### 安装插件

```bash
# 克隆仓库
git clone https://github.com/zzmzz/cli-everything.git

# 安装 Python 依赖
cd cli-everything
pip install websocket-client requests

# 注册为 Claude Code 插件
claude plugin add /path/to/cli-everything
```

安装完成后，即可在 Claude Code 中使用 `/cli-everything` 命令。

## 快速开始

```bash
# 在 Claude Code 中运行插件命令
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

## 生成的目录结构

```
<site>/agent-harness/
├── setup.py
└── cli_anything/          # PEP 420 命名空间（无 __init__.py）
    └── <site>/
        ├── __init__.py
        ├── __main__.py
        ├── <site>_cli.py  # Click CLI 入口
        ├── core/          # 按 API 域名划分的模块
        ├── utils/         # http_client.py + output.py
        └── tests/
```

## 项目结构

```
cli-everything/
├── .claude-plugin/          # Claude Code 插件定义
├── HARNESS_WEB.md           # Web 逆向工程 SOP
├── commands/
│   └── cli-everything.md    # /cli-everything 命令提示词
├── scripts/
│   ├── chrome_recorder.py   # Chrome CDP 被动录制器
│   ├── browser_server.py    # 浏览器自动化服务端
│   ├── browser_client.py    # 浏览器自动化客户端
│   ├── har_parser.py        # HAR 文件 → API 目录 JSON
│   └── js_analyzer.py       # JS 包 API 提取器
├── templates/               # 代码生成模板
│   ├── setup.py.tpl
│   ├── http_client.py.tpl
│   ├── cli_entry.py.tpl
│   ├── core_module.py.tpl
│   ├── output.py.tpl
│   ├── test_core.py.tpl
│   └── test_e2e.py.tpl
└── meican/                  # 示例：生成的美餐 CLI
    └── agent-harness/
```

## cli-anything vs cli-everything

| 方面 | cli-anything | cli-everything |
|------|-------------|----------------|
| 目标 | 开源 GUI 应用 | 闭源网站 |
| 输入 | 源码 / GitHub URL | 网站 URL（用户操作 Chrome） |
| 分析 | AST、grep、源码阅读 | CDP 录制、JS 分析、参数溯源 |
| 后端 | 真实软件 CLI | HTTP API (requests) |
| 输出 | 相同的 `cli_anything.*` 命名空间包 |

## License

MIT

# bps-proxy：Codex 本地代理与 7897 链式出口

> 这是给已经登录 Codex、需要通过本机代理访问上游的用户使用的本地 Responses 代理。重点支持 Windows 上的“ TUN + 全局 HTTP 代理 7897 + 后续 VPS/其他链式出口”场景。

## 先看这里：它解决什么问题

如果你的网络路径是下面这样，本仓库就是为这个场景整理的：

```
Codex
  └─ http://127.0.0.1:8787/v1
       └─ bps-proxy 上游出口
            └─ 系统 HTTP 代理 http://127.0.0.1:7897
                 └─ TUN/全局代理 → VPS 或其他链式出口 → 上游
```

这里有两个本地端口，职责不能混用：

- `127.0.0.1:8787` 是 Codex 访问的本地代理入口，Codex 的 `base_url` 必须指向它。
- `127.0.0.1:7897` 是 bps-proxy 访问外部上游时使用的 HTTP 出口，不是 Codex 的接口地址。
- TUN、全局代理和后续 VPS 链路继续由你的系统代理软件负责，本项目不会替换或关闭它们。

本项目适合以下用户：

- Windows 上使用 Codex CLI，需要一键写入配置并启动本地代理的人。
- 已经使用 TUN、全局代理或多级代理链，希望外部请求先经过本机 7897 的人。
- 需要在出现网关错误时保留 TUN 直连回退，同时不让 Codex 的 8787 本机请求绕到 7897 的人。
- 希望随时用一个脚本恢复原 Codex 配置的人。

本项目不等于官方 OpenAI API，也不是浏览器网页代理。它读取本机 Codex 登录态，把 Codex 的 Responses 请求转到 BPS/Excel 插件后端；请只在自己信任的电脑上使用。

## 与原仓库的区别

本仓库沿用 `kokojacket/openai-proxy` 的 BPS Responses 转发核心，但针对 Windows Codex 和代理链重新整理了入口、配置和网络行为。原仓库地址是 [kokojacket/openai-proxy](https://github.com/kokojacket/openai-proxy)；当前仓库地址是 [whitenicecoffee/openai-proxy](https://github.com/whitenicecoffee/openai-proxy)。

| 方面 | 原仓库 README 的默认方式 | 当前仓库的方式 |
| --- | --- | --- |
| 适用重点 | 通用本地 BPS 代理 | Windows Codex、TUN、7897 和链式出口 |
| Codex 配置 | 手动写顶层 `openai_base_url` | `start.bat` 自动写入 `openai-proxy` provider，并关闭 WebSocket，使用 HTTP/SSE |
| Windows 启动 | 直接启动服务 | `start.bat` 一次完成配置和启动；`A_close.bat` 撤销配置 |
| 本机请求 | 依赖用户自己处理代理绕行 | 自动把 `127.0.0.1`、`localhost`、`::1` 写入 Codex `.env` 的 `NO_PROXY/no_proxy`，并同步 Windows 用户环境变量 |
| 上游出口 | 按原有环境运行 | `system` 模式先走系统/环境代理；Windows 环境变量缺失时读取 Internet Settings；网关或连接失败时回退到 TUN/直连 |
| provider 命名 | 旧配置可能使用保留的 `openai` 表 | 使用自定义 `openai-proxy`，并清理旧版本产生的非法 `[model_providers.openai]` |

原有的 BPS/Excel 后端、Responses、工具转接、图片、压缩请求和模型目录能力仍然保留；变化集中在 Codex 接入、Windows 配置恢复和上游网络路径。

## Windows：推荐使用方式

### 1. 安装

在 PowerShell 中执行：

```powershell
git clone https://github.com/whitenicecoffee/openai-proxy.git
cd openai-proxy
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

如果已经存在 `.venv`，更新代码后重新执行：

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install .
```

### 2. 启动

Windows 只有一个开始入口：`start.bat`。不要在两个 start 脚本之间选择，也不需要 `A_start.bat`。

```bat
.\start.bat
```

`start.bat` 会依次完成：

1. 选择项目 `.venv` 中的 Python；没有虚拟环境时使用系统 `python`。
2. 修改 `%CODEX_HOME%\config.toml`（未设置 `CODEX_HOME` 时为 `%USERPROFILE%\.codex\config.toml`）。
3. 设置 `model_provider = "openai-proxy"`、`base_url = "http://127.0.0.1:8787/v1"`、Responses 协议和 `supports_websockets = false`。
4. 给 Codex 本机地址增加 `NO_PROXY/no_proxy`，避免 Codex 访问 8787 时被系统代理转发到 7897。
5. 启动 bps-proxy，并在窗口中显示实际检测到的上游出口。

配置写入带有 `bps-proxy` 管理标记，重复运行是幂等的，也会保留配置文件里的其他内容。启动脚本不会修改 `auth.json`，不会关闭 TUN，也不会改变 7897 代理软件的规则。

### 3. 完全重启 Codex

启动脚本完成后，完全退出已有的 Codex CLI/终端进程，再重新打开 Codex 并新建对话。旧进程不会重新读取 `%CODEX_HOME%\.env` 和新的 `NO_PROXY`。

### 4. 恢复原配置

先在运行代理的窗口按 `Ctrl+C` 停止服务，再执行：

```bat
.\A_close.bat
```

`A_close.bat` 会恢复 `config.toml` 中原来的 provider、地址和本次加入的 `NO_PROXY/no_proxy`；它不会关闭 TUN 或修改 7897。恢复后重启 Codex 并新建对话。

## TUN、全局 7897 和链式代理

### 推荐模式：`system`

Windows `start.bat` 默认设置 `BPS_UPSTREAM_MODE=system`。在这个模式下，外部上游请求按下面顺序处理：

1. 优先使用 Python 检测到的 HTTP/HTTPS 系统代理；如果系统代理是 7897，就先进入 7897。
2. 如果 7897 返回 502、503、504，或连接本身失败，再尝试操作系统直连路径；在 TUN/全局模式下，这条路径通常仍由 TUN 接管。

因此，使用链式代理时不要把模式改成 `direct`，也不要把 Codex 的地址改成 7897。`NO_PROXY` 只负责本机 8787，外部上游仍然先走 7897。

启动窗口中应看到类似内容：

```
[OK] base_url = http://127.0.0.1:8787/v1
上游网络模式：system first, direct/TUN fallback (outbound proxies=http=http://127.0.0.1:7897,https=http://127.0.0.1:7897)
```

### 四种上游模式

| 环境变量 | 顺序 | 适合场景 |
| --- | --- | --- |
| `BPS_UPSTREAM_MODE=system` | 系统/环境代理优先，网关或连接失败时回退 TUN | 7897 全局出口和链式代理，推荐 |
| `BPS_UPSTREAM_MODE=proxy` | 只使用 `BPS_UPSTREAM_PROXY` | Python 检测不到系统代理，但你明确知道 7897 地址 |
| `BPS_UPSTREAM_MODE=direct` | 只走操作系统直连/TUN | 只希望 TUN 接管，不使用 7897 |
| `BPS_UPSTREAM_MODE=auto` | TUN 优先，失败后再走系统代理 | 不要求 7897 优先的网络 |

如果系统代理未被 Python 检测到，可以显式指定 7897：

```bat
set BPS_UPSTREAM_MODE=proxy
set BPS_UPSTREAM_PROXY=http://127.0.0.1:7897
.\start.bat
```

若启动日志显示 `outbound proxies=none`，不能把它当成“已经走了 7897”；这表示 Python 没有检测到系统代理，需要检查 Windows HTTP/HTTPS 代理设置，或使用上面的显式模式。

## 如何确认链路真的通了

### 先检查本机入口

PowerShell 使用 `curl.exe`，避免 `curl` 被 PowerShell 映射为其他命令：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8787/health
curl.exe --noproxy "*" http://127.0.0.1:8787/v1/models
```

这两项只证明 8787 可以访问，不证明 Codex 登录态或外部上游一定可用。

### 再看代理日志

| 日志 | 含义 |
| --- | --- |
| `outbound proxies=...7897` | Python 检测到了 7897，外部请求会先进入本地 HTTP 代理 |
| `local request GET /v1/models status=200` | Codex 已经访问本机 8787；本机请求没有被错误地送到 7897 |
| `local request POST /v1/responses status=200` | 8787 接受了这一轮 Responses 请求 |
| `relay output ... terminal=response.completed` | 这一轮上游流式响应已经完成 |
| `terminal=response.failed` 后很快出现下一轮 `response.completed` | 某次上游流式请求或重试失败，整体链路仍可能正常；看是否持续失败 |

HTTP 200 只代表本地代理开始返回，判断模型请求是否完成要看 `terminal=response.completed`。

## 502 和链式代理排错

| 现象 | 判断 | 处理 |
| --- | --- | --- |
| Codex 报 502，代理日志里没有 `local request` | Codex 没有访问 8787，通常是旧进程还在使用旧配置或旧环境 | 完全退出 Codex；重新运行 `start.bat`；再启动 Codex 并新建对话 |
| 有 `local request`，但出现 `upstream connection failed` 或 `request failed` | 8787 已经通，问题在 7897、TUN 或后续链式出口 | 先确认 7897 本地代理能用，再检查 TUN/全局模式和链式出口顺序；不要改 Codex 的 8787 地址 |
| `outbound proxies=none` | Python 没有找到系统/环境代理 | 设置 Windows HTTP/HTTPS 系统代理，或显式设置 `BPS_UPSTREAM_MODE=proxy` 和 `BPS_UPSTREAM_PROXY` |
| 启动时提示 `reserved built-in provider IDs: openai` | 旧版本曾覆盖 Codex 保留的 `openai` provider | 更新仓库后重新运行 `start.bat`，它会迁移到 `openai-proxy` 并删除旧表 |
| 8787 端口被占用 | 旧 bps-proxy 进程仍在运行 | 关闭旧代理窗口或结束旧 Python 进程，再运行 `start.bat` |
| 返回 401 | Codex 登录态无效或过期 | 在本机重新登录 Codex；代理不会替你刷新登录令牌 |

## macOS / Linux

安装：

```bash
git clone https://github.com/whitenicecoffee/openai-proxy.git
cd openai-proxy
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

启动：

```bash
./start.sh
```

也可以直接运行：

```bash
python3 -m bps_proxy
```

默认监听 `http://127.0.0.1:8787/v1`。换端口：`python3 -m bps_proxy --port 8788`。Linux/macOS 没有 Windows 的自动配置和撤销脚本，需要手动编辑 `$CODEX_HOME/config.toml`；未设置时使用 `~/.codex/config.toml`。

手动配置：

```toml
model_provider = "openai-proxy"

[model_providers.openai-proxy]
name = "OpenAI Proxy"
base_url = "http://127.0.0.1:8787/v1"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
```

保存后重启 Codex 并新建对话。

## 支持的接口与请求

提供以下本地接口：

- `GET /health`：检查本机代理是否可访问。
- `GET /v1/models`：返回代理内置模型目录。
- `POST /v1/responses`：Responses 请求，支持流式和非流式。
- `POST /v1/responses/compact`：BPS 原生上下文压缩。
- 上述接口也兼容省略 `/v1` 的路径。

协议行为：

- 支持未压缩 JSON，以及 `zstd`、`gzip`、`deflate` 请求体；请求体和解压后内容均最多 32 MiB。
- 支持字符串或消息数组形式的 `input`，以及布尔 `stream`。
- WebSocket 升级请求返回 426，供 Codex 回退到 HTTP/SSE。
- 不支持 `previous_response_id`、`conversation` 或 `background = true` 异步响应；这些请求返回 400。
- 不接受分块上传；请求需要 `Content-Length`。
- 网络超时通常返回 504，其他上游连接错误返回 502；流式响应开始后会通过 SSE 的 `response.failed` 表示失败。

### 工具与图片

- 同时识别顶层 `tools` 和 Responses Lite 的 developer `additional_tools`。
- 支持普通函数工具和 `custom` 文本工具；工具调用由客户端执行，代理负责转发和回放。
- 工具结果保留文本和图片内容；未声明的工具不会被自动启用。
- 支持 PNG、JPEG、GIF、WebP；单张图片最多 20 MiB，解码后合计最多 32 MiB。
- 图片被上游拒绝时，会尝试上传到 BPS 附件接口并复用同账号缓存；上传失败不会静默丢弃图片。

## 模型、档位与目录

内置目录包含：`gpt-6-astra`、`gpt-5.6-sol`、`gpt-5.6-luna`、`gpt-5.6-terra`。默认模型是 `gpt-5.6-sol`，实际可用性取决于上游和账号权限。

请求型号映射：

| Codex 请求型号 | 实际转发型号 |
| --- | --- |
| `gpt-6-sol` | `gpt-5.6-sol` |
| `gpt-6-terra` | `gpt-5.6-terra` |
| `gpt-6-luna` | `gpt-5.6-luna` |

`gpt-6-astra` 和已有 `gpt-5.6-*` 型号保持原样。以上是固定别名映射，不是请求失败后的降级；上游失败时不会偷偷换模型。

`low`、`medium`、`high`、`xhigh`、`ultra` 原样转发；Codex 的 `max` 会映射为 `xhigh`。

模型目录来自随包发布的 Codex 0.155.0 快照，启动日志会显示目录版本和校验值。如需指定其他目录：

```bash
python3 -m bps_proxy --model-catalog /path/to/catalog.json
```

也可以使用环境变量 `BPS_MODEL_CATALOG`。目录必须包含所需模型及完整元数据，修改后需要重启代理。

## 并发、限速与流式大小

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--max-concurrent` | 8 | 同时处理的模型请求数 |
| `--max-pending` | 32 | 活动请求满后的等待队列容量 |
| `--queue-timeout` | 120 秒 | 排队最长等待时间 |
| `--upstream-rps` | 5 | 滚动 1 秒内最多发起的上游请求数 |
| `--max-sse-event-mib` | 16 | 上游 SSE 单行和单事件大小上限 |

这些限制在一个代理进程内共享，重试和实际附件上传也会占用上游发起额度。健康检查和模型目录查询不占模型请求名额。

示例：

```bash
./start.sh --max-concurrent 8 --max-pending 32 --queue-timeout 120 --upstream-rps 5 --max-sse-event-mib 32
```

## 登录态、缓存与安全

- 默认读取 `~/.codex/auth.json`；设置 `CODEX_HOME` 时读取对应目录的 `auth.json`。
- 工具回放缓存默认是 `~/.bps-proxy/calls.json`，可用 `--state /path/to/calls.json` 修改。
- 缓存可能包含完整工具调用、`id`、`summary` 和 `references`，不要上传、提交或分享。
- 服务只允许绑定回环地址（`127.0.0.1`、`localhost`、`::1`），不允许绑定 `0.0.0.0` 等外部地址。
- 不要把 `auth.json`、Codex `.env`、请求日志、对话内容或调用缓存提交到 GitHub。

## 更新与恢复

更新代码：

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install .
```

然后停止旧的 Python 代理进程，重新运行 `start.bat`；仅重启 Codex 不会让已经运行的代理进程加载新代码。

恢复官方 Codex 通道：

```bat
.\A_close.bat
```

恢复后重启 Codex 并新建对话。macOS/Linux 手动删除 `model_provider` 和 `[model_providers.openai-proxy]` 整个表，再重启 Codex。

## 开发验证

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时文件、回环 HTTP 服务和模拟上游，不需要真实账号或外部请求。安装 Codex CLI 后还可以运行兼容性探针：

```bash
python3 tools/probe_compatibility.py
```

兼容性说明见 [docs/codex-compatibility.md](docs/codex-compatibility.md)。

## 效果参考

使用本代理后，可以让 Codex 生成 HTML/SVG 等文件；仓库中保留一个鹈鹕驾驶跑车的动画示例：

![GPT-6 Astra 生成的鹈鹕跑车 SVG 动画效果](docs/images/pelican-svg-example.png)

## 贡献与许可证

欢迎通过 [Issues](https://github.com/whitenicecoffee/openai-proxy/issues) 报告问题或提交 Pull Request。问题报告请附 Python 版本、操作系统、复现步骤和已脱敏日志，不要附登录令牌或调用缓存。

本项目采用 [MIT License](LICENSE)。本项目是独立社区项目，与 OpenAI 没有隶属或背书关系；上游接口及账号可用性可能变化。

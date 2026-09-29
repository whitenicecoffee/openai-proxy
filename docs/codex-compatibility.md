# Codex 默认连接兼容

## 配置和范围

用户配置使用自定义 `openai-proxy` provider，把其 `base_url` 指向本机 /v1。
Codex 内置 `openai` provider ID 是保留名称，不能在 `model_providers` 中覆盖。
登录、账户管理和连接器不在模型代理范围内。
现有工具隧道、完整 native item 回放、账号隔离、turn_id 和 agent_iteration 规则保持不变。

## 请求处理

request_body.py 只处理单层 identity、gzip、deflate、zstd。HTTP 接收大小和解压输出均限制在 32 MiB；
zstd 另检查窗口大小。拒绝重复编码、截断、尾随数据和拼接帧，不截断内容后假装成功。
继续要求 Content-Length。已知客户端使用固定长度请求，不新增 chunked 解析器。
新增 zstandard 依赖，保持 Python 3.9 最低版本和现有标准库 HTTP 服务。

## 原生 compact

POST /responses/compact 和 /v1/responses/compact 复用图片处理、历史重放、SSE 解析和账号隔离。
新版 Codex 在普通 /responses 中发送 compaction_trigger 时，也应用相同的压缩校验与工具禁用规则；
这种路径保持普通 Responses 响应结构，不改成独立 compact 端点的 JSON 对象。
请求末尾追加唯一 compaction_trigger；本次禁用客户端工具，但不清除会话工具目录。
上游仍使用 BPS 的 responses 地址。压缩请求不进入 Office 调用纠正或漏工具重试。

非流式结果规范化为 response.compaction，要求恰好一个包含非空 encrypted_content 的原生 compaction item。
其余输出顺序和不透明内容原样保留。拒绝普通摘要冒充压缩结果；失败、incomplete 和断流不能成为成功结果。
压缩请求不添加普通回答或工具转接提示。流式请求保留 response.created，缓存后续项直至完成校验（最多 4096 项及 32 MiB）；要求 output_item.done 恰好一条 compaction，且密文和标识与最终结果一致。其他允许的输出项仍保留。
客户端显式提供的 context_management 原样传递，省略时沿用既有默认阈值。

## 模型目录和传输

/models 同时保留 OpenAI data 列表与 Codex models 列表。
目录来自 bps_proxy/data/model_catalog.json 的版本化快照：2026-09-27 导入的 Codex 0.155.0 模型缓存。
白名单保留四个模型的完整 model_messages，兼容 base_instructions，truncation_policy 为 tokens / 10000，use_responses_lite 明确为 true。
模型数据 SHA-256：744fb342018b0dcbce2d8cbef98725ea9492a5e3b03f9c5d468962786d91ac85。
仅掩蔽未实现的实验工具、服务端搜索、实验上下文与 reasoning.summary 能力，保留原有 effort 别名和压缩阈值。
--model-catalog 或 BPS_MODEL_CATALOG 可显式选择目录；读取结果按进程固定，损坏文件不回退到简短模板。
tools/import_model_catalog.py SOURCE --output NEW_FILE 可重现白名单导入、来源版本与哈希，不读取认证文件，不覆盖已有输出。

顶层 tools 和 developer additional_tools 都进入同一声明校验；保留 custom 格式与 namespace 内的函数。
同名同定义去重，冲突声明拒绝；user/assistant additional_tools 不授权。顶层空 tools 保留旧续接语义，Lite 显式空目录撤销缓存。
当前请求 tool_choice=none 仅禁用本次工具；缓存仍按账号和会话隔离。additional_tools 解析后不原样发送给 BPS。

Windows 一键配置会选择自定义 `openai-proxy` provider，并写入本机 `base_url`、`requires_openai_auth = true` 和 `supports_websockets = false`，直接使用 HTTP/SSE，避免旧版 Codex 反复预连接 WebSocket；A_close 会恢复原值。绝不写入保留的 `[model_providers.openai]` 表。未关闭 WebSocket 的客户端仍可通过 GET /responses 的 426 回退 HTTP/SSE。
不创建 BPS 上游 WebSocket，不在提交后跨传输重放生成请求。
不实现 previous_response_id 服务端会话存储；客户端继续发送完整或已压缩的历史。

请求型号 gpt-6-sol、gpt-6-luna、gpt-6-terra 固定映射至同名的 gpt-5.6 型号；gpt-6-astra 和原有 5.6 型号不变。
这是请求进入上游前的固定映射；失败后不切换到其他型号。日志同时记录请求与实际型号。

## 并发与诊断

Admission 使用有界 FIFO，默认请求并发 8、等待 32、排队截止 120 秒，排队期间不读取完整请求体。
CLI 参数 --max-concurrent、--max-pending、--queue-timeout 可覆盖；队满或超时返回 503 与 Retry-After: 1。
上游发起采用滚动 1 秒窗口，默认最多 5 次（--upstream-rps）；首次生成、每次重试和实际附件上传共用服务实例的额度。限速等待保持 FIFO，可响应已知连接重置及服务关闭；缓存命中不计数。
参数参考 ghcp_proxy 的 Excel HTTP/1.1 连接池容量 8 与每秒 5 次发起策略；本代理以请求准入控制并发，并非复刻其 HTTPX 连接池。这不是官方上游额度声明。
参考修订：Nonary/ghcp_proxy@dfb758b181e5caa6c52183ef957232140c384dcb，proxy.py、constants.py、rate_limiting.py。
认证失败、上游异常及已知连接重置释放容量；合法 HTTP 半关闭继续处理。无法把正常 FIN 一概认定为取消，不能据此声称即时识别所有客户端离开。
日志记录构建摘要、目录版本/哈希、请求种类、声明来源/数量、有效工具数、实际型号、编码、排队指标和上游限速等待时间。
会话标识使用短哈希；未知路由仅记 other，WebSocket 升级单独记布尔值，不输出原始路径、查询、提示、工具脚本或凭据。
Origin 守卫与 TLS 失败语义保留；inner_json 仍使用原有有界纠正，不执行代理端脚本。

SSE 单行及聚合事件默认均限 16 MiB，CLI `--max-sse-event-mib` 接受正整数；服务实例各自持有配置，不修改进程全局值。单行按原始字节（含字段前缀和行结束符）计数，聚合事件按拼接后的 UTF-8 data 内容（含行间换行）计数，空 data 行也占换行字节。读取队列仍最多保存 8 行，调高大小上限会增加潜在内存占用。

大小超限日志单独记录 `request_id`、`event_type`、`limit_kind`、`observed_bytes` 与 `limit_bytes`，不归类成网络连接错误。行超限只读取上限加 1 字节，已观测值是完整大小的下界；成功解析的至少 4 MiB 事件记录实际 `event_bytes`。事件类型仅输出允许列表中的名称，其余记为 other；无 event 字段且尚未解析 data 时记为 SSE 默认类型 message。日志不包含响应正文或加密推理内容。超限仍沿用 502 / 流已开始后的 response.failed，不补造 response.completed。

## 验证

- tests/test_lite.py、tests/test_catalog.py：四模型冷缓存、声明隔离、模板与版本快照。
- tests/test_admission.py、tests/test_admission_http.py：FIFO、队满、超时、认证失败、异常、RST 与半关闭。
- tests/test_rate_limit.py：滚动窗口、等待取消与关闭、重试和上传计数；HTTP 测试覆盖 8 路活动请求与第 9 路排队。
- tests/test_request_body.py：编码、大小、损坏和多帧边界。
- tests/test_sse_limits.py、tests/test_diagnostics.py、tests/test_cli.py：5 MiB 事件转发、16 MiB 默认上限、逐行与聚合字节边界、日志脱敏、配置隔离和 HTTP / SSE 失败终态。
- tests/test_compatibility.py：真实回环 HTTP、compact 状态、工具目录与不透明历史往返。
- tools/probe_compatibility.py：已安装 Codex、临时用户配置和模拟上游的完整连接验收。
- 原有工具、图片、隔离和终态测试继续运行。

离线测试使用模拟上游，不能证明真实账号的模型权限、BPS 压缩质量或上游容量。上线后的请求完成情况需对照实际客户端与代理日志确认。

## 参考

- https://learn.chatgpt.com/docs/config-file/config-advanced
- https://developers.openai.com/api/reference/resources/responses/streaming-events
- openai/codex rust-v0.154.0：protocol/src/openai_models.rs、codex-api/src/endpoint/compact.rs、core/src/client.rs。
- ranxi2001/sub2api 00bdb50：原生 compaction_trigger 适配思路。
- ranxi2001/sub2api fe27f9895a75e12562f33eff70f21c660329a684：basispoints/stream.go 的 16 MiB 单行与聚合事件限制。
- Kaixxrua/excel-codex-bridge 8a277df：有大小保护的请求解压思路。

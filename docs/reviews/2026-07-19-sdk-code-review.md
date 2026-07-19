# mcbe-ws-sdk 全量代码审查报告

## 1. 审查信息

- 审查日期：2026-07-19
- SDK 快照：`d29f6f8317e79cc46f8e7f586c0ea23d27469d94`
- 重点代码快照：`12d66aaab68767143ce5ef79e024587bdea28433`
- 审查方式：静态代码审查、主仓行为对照、单元测试、类型检查、Lint、打包内容检查
- 审查对象：Python SDK、TypeScript Addon、测试、示例、文档和发布配置

本报告暂不把主仓迁移或主仓接入 SDK 作为当前整改范围。主仓代码仅用于确认原有 MCBE WebSocket 行为、协议字段和默认配置，避免抽取过程中遗漏功能。

## 2. 总体结论

当前实现具备清晰的包结构，也已经抽出了部分可复用能力，但尚未形成可运行、可扩展、可发布的 SDK 闭环。

结论：**不建议按当前状态发布 `0.1.0`。**

主要阻塞点不是单纯的 AI 命名残留，而是：

1. 默认 Python facade 没有完成 MCBE 初始化和事件订阅。
2. 发布的 Addon 没有注册响应发送器，capability 请求无法返回。
3. 对外声明的 EventBus 和 CapabilityRegistry 没有接入生产路径。
4. Addon 分片没有遵守最终命令的 UTF-8 字节预算。
5. AI/Agent 宿主模型仍进入了 SDK 核心公共 API。
6. 异步任务、分片缓存、异常隔离和发布物边界不满足 SDK 生产要求。

## 3. 已做得较好的部分

- Python `tellraw`、`scriptevent` 和 raw command 已统一经过 `FlowControlMiddleware`，并按最终 `commandLine` 的 UTF-8 字节数校验。
- `AddonBridgeService` 已移除主仓的模块级全局单例，配置和实例生命周期可以独立注入。
- 普通 `PlayerMessageEvent` 会把当前 `sender` 直接传给 hook，避免依赖连接级最近玩家进行普通消息路由。
- Python 包的职责目录划分基本合理：`protocol`、`flow`、`delivery`、`gateway`、`addon`、`capability`。
- Python 源码通过严格 mypy；大部分单元测试、Ruff 和 TypeScript 类型检查均可通过。
- tellraw target 和 scriptevent message ID 已具备基本的命令注入防护。

这些基础可以保留，但在冻结公共 API 前需要先修正以下问题。

## 4. 严重问题

### C-01 发布 Addon 不会返回 capability 响应

**证据**

- `addon/scripts/main.ts:7-13` 的 world-ready 回调只有注释示例。
- `addon/scripts/bridge/router.ts:73-75` 仅在 `responseSender` 已注册时发送响应。
- `initializeToolPlayer()`、`setResponseSender(sendBridgeResponseChunks)` 在生产入口中均未调用。

**实际行为**

Addon 能收到 `mcbeai:bridge_request`，也可能执行内置 capability，但结果会被静默丢弃。Python `AddonBridgeService` 等待 `MCBEAI|RESP`，最终只能超时。

**建议**

- 在发布入口中完成工具玩家和 response sender 的默认 wiring。
- 如果 Addon 只是源代码模板而不是可直接安装的包，应从行为包发布物中移除，并在文档中明确要求宿主创建入口。
- 为真实构建后的 `main.js` 增加端到端测试，验证 request 必然产生 success 或 error response。

### C-02 默认 McbeServerFacade 没有完成 MCBE 初始化和订阅

**证据**

- `src/mcbe_ws_sdk/gateway/server_facade.py:160-180` 只调用 `hook.on_connected()`，随后进入收包循环。
- `src/mcbe_ws_sdk/gateway/hook.py:79-80` 的默认 hook 不发送任何帧。
- SDK 虽在 `gateway/handler.py:95-98` 提供了 subscribe encoder，但 facade 未调用。
- README quickstart 的 hook 也只打印日志。

**实际行为**

`McbeServerFacade()` 建连后不会发送 `{"Result":"true"}`，也不会订阅 `PlayerMessage`。真实 MCBE 客户端不会向该 quickstart 推送玩家消息。

**建议**

- 把初始化帧和 `PlayerMessage` 订阅定义为 facade 所拥有的中立协议握手。
- welcome、登录和业务提示继续交给宿主 hook。
- 增加真实 WebSocket 或高保真 fake transport 测试，断言连接后的首帧顺序。

### C-03 Addon 分片不满足字节安全契约

**证据**

- `addon/scripts/bridge/chunking.ts:11-23` 按 JavaScript string length 切分，即 UTF-16 code unit 数量。
- `addon/scripts/bridge/toolPlayer.ts:60-63` 在包装成 `tell @s <chunk>` 后没有重新检查字节长度。

**实际行为**

- 256 个汉字包装后约为 831 UTF-8 bytes，明显超过当前协议采用的 461B 安全预算。
- `"x".repeat(255) + "😀"` 可把 emoji 的 surrogate pair 拆到两个分片中。
- request ID、prefix 和命令 wrapper 开销没有计入分片预算。

**建议**

- 以最终 `tell @s <framing>` 命令为探针，按 UTF-8 byte length 反推每片内容。
- 按 Unicode code point 遍历，不按 UTF-16 下标直接 `slice`。
- Python 和 TypeScript 共用同一组协议向量，覆盖中文、emoji、转义字符和长 ID。

### C-04 EventBus 的语义事件未接入生产路径

**证据**

- `src/mcbe_ws_sdk/gateway/events.py:25-36` 声明了 9 类事件。
- 生产代码只在 `gateway/connection.py:127,142` 发出 CONNECTED/DISCONNECTED。
- `gateway/server_facade.py:184,265` 只发出 RAW_INBOUND/RAW_OUTBOUND。
- PLAYER_MESSAGE、BRIDGE_CHUNK、UI_CHAT_CHUNK、UI_CHAT_REASSEMBLED 和 COMMAND_RESPONSE 没有 emit 调用。

**影响**

README 所称的“双层接口”并不存在。低层订阅者只能看到原始帧，无法消费已解析事件。

**建议**

- 在每个协议分支 emit 对应的 typed event。
- 所有连接相关事件统一携带 `ConnectionState`。
- 用 facade 集成测试验证事件产生，而不是只直接测试 `bus.emit()`。

### C-05 CapabilityRegistry 是未接线的公共参数

**证据**

- `gateway/server_facade.py:74,80-82` 接受并保存 `capabilities`。
- bridge branch `gateway/server_facade.py:231-237` 只调用 hook，从未调用 `_capabilities.handle()`。
- `examples/capability-greeting` 注入 registry 后只启动和停止端口。

**影响**

`McbeServerFacade(capabilities=registry)` 给使用者造成完整接线的错误预期。示例名称与实际行为不符。

**建议**

在冻结 API 前明确 capability 方向：

- Python -> Addon capability：Python 只需要 `AddonBridgeClient`，具体 handler 应在 Addon 注册。
- Addon -> Python capability：需要定义真正可到达 Python 的传输帧、caller identity、response encoder 和错误响应。

如果当前协议只有 Python -> Addon 方向，应删除 facade 的 `capabilities` 参数和不可达的 Python registry，而不是保留半闭环抽象。

### C-06 sdist 发布物边界失控

**证据**

- `pyproject.toml:23-24` 只声明 wheel package，没有 sdist include/exclude。
- 实际检查得到约 29MB、6832 个文件的 sdist，包含 `addon/node_modules`、构建目录等内容。
- wheel 约 43KB，与 sdist 的内容边界明显不一致。

**建议**

- 显式声明 sdist 文件集。
- 排除 `node_modules/`、`lib/`、`dist/`、缓存、本地环境文件和临时产物。
- 发布门禁中列出 wheel/sdist 内容，并设置体积和文件数量上限。

## 5. 重要问题

### I-01 AI 和 Agent 业务仍进入 SDK 核心

以下概念不是 MCBE WebSocket 协议的通用组成部分：

- `flow/flow_control.py:128-206`：`chunk_ai_response()`、`role`、`assistant`。
- `gateway/messages.py:19-27`：reasoning、thinking、tool_call、tool_result。
- `gateway/messages.py:42-44`：tool name、arguments、result preview。
- `gateway/sink.py:29-36`：`AI_RESPONSE_SYNC`。
- `gateway/connection.py:47-48`：`context_enabled`、`custom_variables`。
- `protocol/minecraft.py:177-192`：LLM、思考和工具调用语义注释及前缀。

**建议**

- 核心 flow 只负责“文本 + wrapper probe -> 安全分片”。
- `mcbeai:ai_resp` 放入可选、带版本的 legacy addon profile。
- reasoning/tool/context 等结构移回宿主，或由宿主自定义 response envelope。

### I-02 ai_resp_message_id 配置无效，delivery 也不完整

**证据**

- `config.py:12` 暴露 `ai_resp_message_id`。
- `flow/flow_control.py:203-206` 硬编码 `mcbeai:ai_resp`。
- SDK `delivery/outbound.py` 没有主仓已有的 `send_ai_response()`、assistant prelude delay 和统一 ai_resp 节流。

**建议**

- 不让通用 flow 直接读取 AI 协议配置。
- legacy profile 的 encoder 显式接收完整协议配置。
- 如果保留 response sync，统一由 delivery 执行编码、节流和发送。

### I-03 默认 run_world_command 安全边界不足

**证据**

- `addon/scripts/bridge/capabilities/index.ts:12-16` 默认启用 `run_world_command`。
- `commandSafety.ts:1` 仅拒绝 stop、reload、kick、op、deop。
- router 接受所有 `sourceType === "Server"` 的同 ID scriptevent。

`sourceType` 只描述命令来源，不是身份认证或授权结果。现有 denylist 仍允许 `kill`、`fill`、`clear`、`tp`、`summon`、`function`、`gamerule` 等高影响命令。

**建议**

- 默认不注册任何会修改世界的 capability。
- 由宿主显式 opt-in，并提供 capability ACL。
- 若需要命令能力，采用 allowlist 和参数级校验，不依赖少量黑名单。
- 默认只接受受控 Server 路径；不要只通过实体名字识别可信工具玩家。

### I-04 bridge 请求缺少调用者身份和协议版本

当前 request 只有 `request_id`、`capability` 和 `payload`，无法表达：

- 触发玩家是谁。
- 请求属于哪个连接或会话。
- 调用者具有什么权限。
- 使用哪个协议版本和 capability schema 版本。

这也是当前无法安全实现 `find_entities`、玩家级授权和多人审计的根因。

**建议**

引入版本化 envelope，至少包含：`version`、`request_id`、`capability`、`caller`、`payload`。`caller` 必须来自可信传输上下文，不能由 payload 自报。

### I-05 畸形 bridge/UI 分片会断开共享连接

`addon/service.py:106-130` 的 codec 异常没有在单帧边界隔离。类似 `MCBEAI|UI_CHAT|bad` 的消息会让异常穿透 `_handle_raw()`，最终退出整个 WebSocket 消息循环。

**建议**

- 在每帧路由边界捕获 `ProtocolError`。
- 记录有限元数据后丢弃该帧，继续处理同一连接后续消息。
- 不要用宽泛 `ValueError` 表达所有协议失败；建立 SDK 异常层级。

### I-06 请求、任务和 buffer 生命周期不完整

**证据**

- `addon/service.py:69-90` 在 `send_command()` 抛错或调用方取消时不会清理 pending request。
- `addon/service.py:140-141` 创建未跟踪 callback task。
- `addon/session.py:47-49` 的 request、bridge chunk、UI chunk 缓存无 TTL、条目或总字节限制。
- `addon/scripts/bridge/responseSync.ts:8` 的 AI buffer 同样无界。
- `gateway/connection.py:137-142` cancel sender task 后没有 await。
- `server_facade.py:90,149` 的 stop event 不会清空，实例重启语义不明确。

**建议**

- 所有 request 路径使用 `try/finally` 清理 pending 和 chunk buffer。
- 维护 background task 集合，关闭时 cancel 并 await。
- 为 buffer 增加 TTL、最大 ID 数、最大 `n`、单消息字节数和全局字节数。
- 明确 facade 是单次生命周期实例，或支持可靠 restart。

### I-07 EventBus 弱订阅无法退订

`events.py:52` 创建 wrapper，但没有保存原 handler；`events.py:122-123` 却依赖不存在的 `__wrapped__` 匹配。因此默认 `weak=True` 的订阅调用 `unsubscribe()` 会返回 0。

同时 RAW_INBOUND 传 `(state, raw)`，RAW_OUTBOUND 只传 `payload`，多连接订阅者无法关联出站帧。

**建议**

- 返回显式 subscription token，或存储包含原 handler weakref 的记录对象。
- 自动清除已死亡 weakref。
- 统一所有连接事件的第一个参数为 `ConnectionState`。

### I-08 WebSocket transport 配置迁移不完整

SDK `WebsocketTransportConfig` 只有 host/port，遗漏原实现的：

- `ping_interval=30`
- `ping_timeout=15`
- `close_timeout=15`
- `max_size=10 MiB`
- `max_queue=32`

**影响**

运行行为依赖安装的 `websockets` 版本默认值，升级依赖可能静默改变心跳、背压和最大帧行为。

**建议**

补齐并显式透传 transport knobs。当前 `websockets>=12` 覆盖多个 major，应建立 12/14/16 兼容测试，或缩小支持范围。

### I-09 Minecraft error 和 commandResponse 覆盖不完整

- `MinecraftHeader.messagePurpose` 没有 `error`。
- facade 不会向 hook/event bus 传递 error envelope。
- `_extract_command_response()` 文档声称传完整 body，实际只返回 statusCode/statusMessage。
- Pydantic 默认 `extra="ignore"`，与“保留扩展字段”的设计承诺冲突。

**建议**

- 增加 typed error frame/event。
- commandResponse 默认保留完整 body，并提供状态字段便捷访问。
- 对需要向前兼容的 wire model 使用明确的 `extra="allow"` 策略。

### I-10 TypeScript router 缺少 schema 和异常闭环

`router.ts:53-75` 未隔离以下失败：

- JSON parse 失败。
- payload 不是 object。
- 缺少 request ID 或 capability。
- capability handler 抛错。
- response sender 抛错。

订阅回调使用 `void handleBridgeScriptEvent(event)`，异常会变成未处理 Promise rejection，Python 端也收不到结构化错误。

**建议**

- 解析后执行完整 schema validation。
- 对含合法 request ID 的失败尽量返回 `{ok:false,error:{code,message}}`。
- 统一限制日志中的 payload 和错误详情，避免泄露世界数据。

### I-11 Addon 自动化测试为空

`addon/package.json` 配置了 Vitest，但仓库没有任何 `.test.ts` 或 `.spec.ts`。`vitest run` 直接以 code 1 退出。

至少需要覆盖：

- 默认入口 wiring。
- request success/error response。
- malformed JSON/schema。
- source type 和 caller 校验。
- 中文、emoji、长 ID 和转义字符分片。
- 分片乱序、重复、metadata 冲突和 TTL。
- world command 默认关闭及 allowlist。

### I-12 公共 API、类型发布和版本来源不稳定

- README 称 `CommandRegistry`、flow、delivery 等属于公共表面，但顶层导出不完整。
- `protocol/__init__.py` 为空，import policy 不明确。
- `__version__` 和 `pyproject.toml` 重复维护。
- 包内缺少 `py.typed`，类型信息不会按 PEP 561 稳定发布。
- 没有异常层级，调用者只能捕获 `RuntimeError`、`ValueError` 或匹配中文字符串。

**建议**

- 在 0.1.0 前冻结公开 import 路径。
- 增加 `py.typed`。
- 版本从 package metadata 单一读取。
- 提供 `McbeWsSdkError`、`ProtocolError`、`FrameTooLargeError`、`BridgeTimeoutError`、`BridgeClosedError` 等异常。

### I-13 默认命令表仍包含宿主业务

`command/registry.py:29-43` 默认内置中文 `#登录`、`运行命令`、`帮助`，但 SDK 又声明不拥有认证和世界业务。

**建议**

默认 registry 为空，或只提供 registry 机制。登录、帮助内容和运行命令均由宿主注册。

### I-14 设计文档与实现存在漂移

已确认的差异包括：

- 规格写有 `unregister/dispatch`，实现为 `register/handle`。
- 规格承诺保留额外协议字段，当前模型会丢弃。
- 规格提到 `from_settings()` 兼容工厂，实际不存在。
- PRD 声明不内置具体 capability，但 Addon 已默认内置三项能力。
- PRD 列出 `CapabilityResult`，实现中不存在。
- README 称默认 facade 可工作，实际没有协议握手。

公共 API 修正后应同步更新 PRD、spec、README 和示例，避免以过期文档作为兼容承诺。

### I-15 Addon 依赖版本和 lockfile 不一致

`package-lock.json`、`pnpm-lock.yaml` 和 behavior pack manifest 对 `@minecraft/server`、`@minecraft/server-ui` 的版本并不一致。不同包管理器安装会得到不同的类型和 API 基线。

**建议**

- 选择一个包管理器和 lockfile。
- 对齐 package manifest、行为包 manifest、最低引擎版本和 CI 安装方式。
- 明确 `@minecraft/server-gametest` beta 依赖对稳定版 MCBE 的影响。

## 6. 次要问题

### M-01 完整出站 payload 在 info 级别记录

`delivery/outbound.py:96-102` 会记录完整玩家文本和命令。默认日志应只记录 request ID、类型、长度和字节数，原文仅在显式 debug/trace 模式启用。

### M-02 flow 保留死实现和无效参数

`flow_control.py:349-451` 保留了一套主要 `chunk_*` 路径不再使用的旧分片实现；`chunk_raw_command(max_length=...)` 的参数也未使用。应在冻结 API 前删除或明确用途。

### M-03 配置只有浅层不可变

`FlowControlSettings` 虽是 frozen dataclass，但 `chunk_delays` 是可变 dict；端口、预算、超时和延迟也没有值域校验。

### M-04 pydantic-settings 是未使用的运行时依赖

源码没有导入 `pydantic-settings`。如果 SDK 坚持使用显式 dataclass 配置，应移除该依赖；如果需要环境配置，应实现带 SDK prefix 的可选 adapter，避免读取宿主环境时发生字段碰撞。

### M-05 addon/.env 被版本控制跟踪

当前文件只含非敏感构建配置，但仍违反仓库“不提交 `.env`”的约定。应只保留 `.env.example`。

### M-06 capability-greeting 示例本身不可运行完整流程

- registry 没有被 facade 使用。
- `greeting.py` 用标准库 logging 传入 structlog 风格关键字参数，handler 真执行时会抛 `TypeError`。
- 示例使用 9876 端口，文档输出却写 8080。
- Ruff 可发现一个未使用导入。

## 7. 协议覆盖矩阵

| 协议能力 | 当前状态 | 主要缺口 |
| --- | --- | --- |
| WebSocket accept/lifetime | 部分覆盖 | stop/restart 和任务回收语义不完整 |
| MCBE 初始化帧 | 未接线 | facade 不发送 `{"Result":"true"}` |
| PlayerMessage subscribe | encoder 已有，运行时未接线 | 默认 facade 不订阅 |
| PlayerMessage parse | 已覆盖 | EventBus 不 emit；慢 hook 会阻塞共享连接 |
| commandRequest | 基本覆盖 | transport 参数和错误类型不完整 |
| commandResponse | 部分覆盖 | body 字段被裁剪，EventBus 不 emit |
| error envelope | 未覆盖 | 无模型、hook 或 event |
| tellraw flow | Python 已覆盖 | Addon 侧不是同一字节安全算法 |
| scriptevent flow | Python 已覆盖 | legacy response ID 配置未真正生效 |
| raw command | 已覆盖 | 只有通用 `ValueError`，无 SDK 异常类型 |
| Addon request/RESP | codec/session 已覆盖 | 默认 Addon 不回包、异常和取消会泄漏 pending |
| UI_CHAT | 基本覆盖 | callback 未跟踪，buffer 无界，缺调用者认证 |
| AI_RESP | wire codec 部分覆盖 | 强耦合 AI、delivery 缺失、Addon buffer 校验不足 |
| EventBus | 4/9 事件接线 | 核心 typed event 缺失，弱订阅退订失效 |
| CapabilityRegistry | 独立单测覆盖 | facade 生产路径完全未使用 |
| 多玩家隔离 | 普通消息部分覆盖 | bridge 无 caller identity，无法玩家级授权 |
| 协议演进 | 未覆盖 | 没有 wire version，模型扩展字段被丢弃 |

## 8. AI 残留分类

### 8.1 为兼容现有线协议暂时保留

- `mcbeai:bridge_request`
- `MCBEAI|RESP`
- `MCBEAI|UI_CHAT`
- `MCBEAI_TOOL`
- `mcbeai:ai_resp` 及 `id/i/n/p/r/c` 紧凑字段

这些值已经与现有 Addon 对接，不能直接改名。建议将它们定义为 `legacy_mcbeai_v1` profile，并明确兼容期限和弃用策略。

### 8.2 应泛化后保留能力

- `chunk_ai_response` -> 通用 framed event chunking。
- `AI_RESPONSE_SYNC` -> addon event sync / framed response。
- `role` -> channel/kind 或由 profile 自定义的 metadata。
- `ai_resp`/`ai_resp_prelude` -> profile-specific delay key。
- `UiChat` -> generic inbound UI/user message channel。
- `bridge_tool_player_name` -> bridge sender identity。

### 8.3 应移出 SDK 核心

- reasoning、thinking、tool_call、tool_result。
- tool name、tool args、tool result preview。
- `PlayerSession.context_enabled` 和 `custom_variables`。
- LLM、思考、工具调用相关 MCColor/MCPrefix 注释和默认行为。
- `game_message`、`ai_response_sync` 等宿主 dict 路由。
- 默认登录命令和登录隐藏规则。
- 文档中的 Agent、LLM worker face 等宿主术语。

## 9. 建议目标边界

建议把 SDK 分成三个明确层次：

### 9.1 Core

- MCBE WebSocket envelope。
- subscribe、commandRequest、commandResponse、error。
- byte-safe command chunking。
- transport lifetime 和 typed EventBus。
- 不包含 AI、登录、provider、conversation 或具体世界 capability。

### 9.2 Protocol profiles

- `legacy_mcbeai_v1`：保存现有 bridge/UI_CHAT/AI_RESP 兼容协议。
- profile 自己定义 message ID、prefix、metadata schema、encoder/decoder 和 delay key。
- core flow 只接收 wrapper probe，不认识 AI 字段。

### 9.3 Addon reference implementation

- 默认只启用无副作用的 transport 和 codec。
- 查询/修改世界 capability 作为显式 opt-in 示例。
- 不把 GameTest beta simulated player 隐藏在通用稳定 API 之下。
- 与 Python profile 共享协议测试向量。

## 10. 整改优先级

### P0：恢复可运行闭环和安全边界

1. 确定 facade 是否自动发送初始化和 subscribe，并修正文档/测试。
2. 完成 Addon response sender 和工具玩家 wiring，或停止发布不可直接运行的 Addon。
3. 修复 Addon UTF-8 字节安全和 Unicode 分片。
4. 接通或删除 EventBus 未实现事件。
5. 明确 capability 方向，接通或删除 facade 的 Python CapabilityRegistry。
6. 默认禁用 `run_world_command`，建立 opt-in ACL/allowlist。
7. 对单帧协议错误做隔离，不能断开共享世界连接。
8. 修复 pending、task 和 buffer 的清理及限额。
9. 限制 sdist 内容。

**P0 完成标准**

- README quickstart 能与高保真 MCBE fake 或真实客户端完成 subscribe -> PlayerMessage。
- Python request 在 Addon 端必然得到 success/error response，不依赖超时收尾。
- 中文、emoji 和长 metadata 的所有最终命令均不超过配置预算。
- malformed bridge/UI frame 不影响后续合法消息。
- 默认安装不暴露世界修改 capability。
- wheel/sdist 只包含声明的发行文件。

### P1：冻结 SDK 公共 API 和协议边界

1. 抽出 `legacy_mcbeai_v1` profile。
2. 从 core 移除 AI stream、tool、context 和默认登录命令。
3. 补齐 error、完整 commandResponse 和 wire version。
4. 统一 typed event 签名和 EventBus subscription token。
5. 建立 SDK 异常层级。
6. 补齐 transport config 和支持版本矩阵。
7. 冻结顶层 exports、`py.typed` 和版本来源。
8. 同步 PRD、spec、README 和示例。

### P2：发布和长期维护质量

1. 建立 Python 3.11/3.12/3.13/3.14 CI。
2. 建立 `websockets` 支持版本矩阵。
3. 选择唯一 Node 包管理器并统一 lockfile。
4. 增加 Python/TypeScript 共用 wire vectors。
5. 加入覆盖率、wheel/sdist 内容、twine check 和示例 smoke test 门禁。
6. 对日志原文、buffer 指标、协议拒绝原因提供可配置可观察性。

## 11. 建议测试门禁

### Python

```bash
ruff check --no-cache src tests examples
mypy --no-incremental src
pytest -p no:cacheprovider
```

必须新增 facade 握手、typed event、malformed frame 隔离、取消清理、buffer limit、完整 commandResponse 和配置透传测试。

### Addon

```bash
npm run lint
npm exec tsc -- --noEmit
npm test
npm run build:production
```

Vitest 必须真正发现并执行测试，不能以“无测试文件”结束。

### 发布物

```bash
python -m build
twine check dist/*
```

发布任务还应检查：

- wheel/sdist 文件清单和大小。
- 不包含 `.env`、`node_modules`、缓存、日志和构建中间产物。
- wheel 安装后的公开 import 路径。
- `py.typed` 是否存在。
- README 示例能否从干净环境运行。

## 12. 本次验证结果

- Python pytest：100 passed，1 failed。
- 唯一 pytest 失败来自审查沙箱禁止绑定 `127.0.0.1:0`；因此本次没有获得真实 socket lifetime 通过证据。
- 排除该真实 bind 用例后，其余 Python 单元测试通过。
- Ruff：源码和测试通过；包含示例时发现 `greeting.py` 一个未使用 import。
- mypy strict：26 个源码文件通过。
- TypeScript `tsc --noEmit`：通过。
- ESLint：通过。
- Vitest：失败，原因是没有测试文件。
- Hatch wheel/sdist：构建成功，但 sdist 内容污染，见 C-06。
- 未验证：真实 MCBE、真实 `.mcaddon`、Python 3.11、多人并发实机、断线中请求、Unicode 全链路和长时间 buffer 回收。

## 13. 当前阶段不处理的事项

以下事项不作为本报告 P0/P1/P2 的完成条件：

- 主仓改为依赖 `mcbe-ws-sdk`。
- 删除主仓旧 `services/websocket` / `services/addon` 实现。
- 主仓 HostHook/HostSink 适配。
- 主仓业务回归和发布节奏。

在 SDK 自身完成 P0、公共 API 边界稳定并具备可靠发布物后，再单独规划主仓接入更合理。

## 14. 最终判定

**当前状态：Not ready for release。**

推荐先完成 P0，确保默认 facade、Addon 和协议链路真实可运行；随后在 P1 中移除核心 AI/宿主业务残留并冻结公共 API。不要在当前半闭环状态下发布 0.1.0，否则后续修复很可能需要破坏性 API 和线协议调整。

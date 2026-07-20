# mcbe-ws-sdk 全项目 Code Review 报告

> **日期**：2026-07-20  
> **范围**：Python SDK（`src/mcbe_ws_sdk`）、TypeScript Addon（`addon/`）、测试 / CI / 打包 / 文档 / 示例  
> **方法**：对照 PRD、架构文档与源码；按 gateway / flow+delivery / addon bridge / TS addon / 工程完备度 分域审查  
> **版本**：v0.1.0（约 3.4k Python LOC + 1.0k Addon TS LOC；测试约 3.1k + 1.4k）  
> **说明**：同日已有 `2026-07-20-sdk-architecture-review.md`。本报告基于**当前源码**复审，并标注旧报告中**已修复**项，避免误导。

---

## 1. 总体评价

| 维度 | 评分 | 一句话 |
|------|------|--------|
| 架构合理性 | ★★★★☆ | DI + Protocol 分层清晰，网关边界干净；少量契约未落地 |
| 完成度 | ★★★★☆ | 核心路径可跑、可测、可打包；与 PRD 仍有漂移 |
| 正确性 / 鲁棒性 | ★★★★☆ | 分片与 bridge 限流扎实；个别 hook 语义悬空 |
| 测试与 CI | ★★★★☆ | 单元密、矩阵全；缺 coverage 门槛与真机 e2e |
| 文档与 DX | ★★★★☆ | README/mkdocs/示例齐全；PRD 与实现不同步 |
| **综合** | **★★★★☆（中上，接近可独立发布）** | 骨架已是可发布 SDK；修契约与安全清单后可冲 5 星 |

**核心判断**：把 MCBE WebSocket 网关从宿主 AI 业务中抽成独立 SDK 的目标**基本达成**。`ConnectionHook` / `ResponseSink` 依赖反转、无全局单例、461B 字节安全分片、addon 桥会话隔离，都是正确且可维护的设计。当前主要短板不是「缺模块」，而是：

1. **契约写了但未执行**（hook 返回值、命令解析结果被丢弃）  
2. **PRD 与实现漂移**（`PlayerSession` 等）  
3. **Addon 侧安全与状态模型**仍偏脚本式  
4. **工程门槛**（覆盖率、真机 e2e）尚未硬化  

---

## 2. 架构总览（现状）

```text
McbeServerFacade          ← 宿主入口；拥有完整 WS 生命周期
├── ConnectionManager     ← 连接 + 每连接 response-sender 协程
│   ├── ConnectionState   ← 传输无关身份（id, send_payload, response_queue）
│   └── ResponseSink      ← 宿主路由 OutboundText / SystemNotification
├── MinecraftProtocolHandler  ← PlayerMessage / 命令解析 / 状态行
│   └── CommandRegistry
├── EventBus              ← WsEventType 进程内 pub/sub
├── ConnectionHook        ← 6 个生命周期钩子（宿主实现）
├── AddonBridgeService    ← ScriptEvent 桥 + 分片重组
│   └── AddonBridgeSession
├── FlowControlMiddleware ← 461B 硬限制分片
└── McbeOutboundDelivery  ← tellraw / scriptevent / raw 统一出站
```

**设计原则落实情况**

| 原则 | 状态 | 证据 |
|------|------|------|
| 依赖反转，不导入宿主 | ✅ | `McbeServerFacade.__init__` 全 keyword + 默认 `NoOpHook` / `DefaultResponseSink` |
| 无全局单例（Python） | ✅ | `AddonBridgeService` 显式构造；测试也验证无 `_player_sessions` 全局桶 |
| 出站统一流控 | ✅ | `McbeOutboundDelivery` → `FlowControlMiddleware` |
| Profile 可替换 | ✅（已改进） | `AddonBridgeProfile` 为 `Protocol`；`McbewsV1Profile` 实现 |
| 多人会话隔离在 SDK | ⚠️ 宿主侧 | SDK 只传 `PlayerMessageEvent.sender`；无 `PlayerSession`（与 PRD 不符，见 §5） |

---

## 3. 分域审查

### 3.1 Gateway 层 — ★★★★☆

**优点**

- **生命周期完整**：`run_lifetime` 单次使用守卫、`stop` → `_graceful_shutdown` → `shutdown_all` 取消 sender，disconnect 路径上 `addon.close_connection` + `drop_connection` + `hook.on_disconnected` 分层 try/except，失败互不拖垮。
- **入站路由清晰**：error → commandResponse → addon bridge/UI → PlayerMessage，分支顺序合理；bridge 前缀与 sender 不匹配时有 `bridge_prefix_not_matched` 诊断日志。
- **EventBus 成熟**：默认弱引用、`SubscriptionToken` 精确退订、handler 异常隔离（per-handler try/except）、同步非 awaitable 返回值会 `TypeError`。
- **`player_name` 防误用**：属性访问触发 `DeprecationWarning`，引导宿主用 `PlayerMessageEvent.sender`。
- **Sink 类型安全**：`RouteEnvelope.from_message` 只接受 SDK 值对象；`dispatch` 用 `TypeError` 而非 `assert`（旧 review 中的 `-O` 问题已修）。

**问题**

| 严重度 | 位置 | 问题 | 失败场景 |
|--------|------|------|----------|
| **高** | `server_facade.py:190-195` + `_handle_raw` | hook / `model_validate` 异常**未隔离**：任一 `on_player_message` / `on_error` / `on_command_response` 抛错会冒泡到连接循环并**拆掉整条 WS** | 宿主 hook 对一条坏消息 raise → 同连接所有玩家断线；畸形 commandResponse 同理。对比 EventBus / addon 分支已有隔离 |
| **高** | `gateway/hook.py:9-11` + `server_facade.py:317` | 文档约定 `on_player_message` 返回 `True` = 已消费、停止默认处理；facade **丢弃返回值**，且本身没有「默认处理」分支 | 宿主返回 `True` 期望短路，实际无任何差异 → 契约谎言，集成时踩坑 |
| **高** | `connection.py:109` | `response_queue = asyncio.Queue()` **无 maxsize**；disconnect 时 cancel sender，队列剩余消息静默丢弃 | LLM 刷屏 + 客户端慢/断 → 内存膨胀；宿主以为已投递，实际未送达且无 nack |
| **中** | `server_facade.py:313-315` | `parse_typed_command()` 结果被丢弃，纯副作用空调用 | 注册了命令的宿主仍须自己再 parse；handler 工作浪费且误导「facade 会解析命令」 |
| **中** | `sink.py:58-76` | `ResponseSink` Protocol **强制要求 `dispatch`** | 宿主只想实现两个 `on_*` 时结构类型不匹配，被迫继承 `DefaultResponseSink` 或复制 boilerplate |
| **中** | bus CONNECTED vs `hook.on_connected` | bus 在 `create_connection`（握手前）发 CONNECTED；hook 在 subscribe 之后 | 宿主若在 bus CONNECTED 就发欢迎/业务包，可能早于 subscribe |
| **中** | `server_facade.py:344-352` | `send_payload` 在连接已 drop 后仍 `websocket.send` | shutdown 竞态：manager 已无连接，sink 仍可能打到半关闭 socket |
| **中** | `connection.py:154-158` | response-sender 用 `wait_for(..., 0.5)` 轮询是否仍在连接表 | 空闲连接每 0.5s 唤醒；cancel 已足够唤醒 `queue.get()` |
| **低** | `server_facade.py:180` | `assert state.send_payload is not None` | 生产 `-O` 下 assert 消失；风格不一致 |
| **低** | 文档写「subscribe + welcome」 | 实际只发 handshake + subscribe；welcome 由宿主 hook 负责 | 文档漂移；示例已正确示范 |

**改进建议**

1. **P0**：`_handle_raw` 内对 hook 与 `model_validate` 做 per-call try/except，对齐 EventBus 隔离策略，禁止单条坏消息拆连接。  
2. **P0**：要么消费 `on_player_message` 的 bool 并定义默认处理，要么改为 `-> None` 并删 consumed 文档。  
3. **P0**：给 `response_queue` 设 `maxsize` + 溢出策略；disconnect 时 drain 或上报丢弃计数。  
4. 把 `ParsedCommand` 放进事件/hook；删除无用 parse 调用。  
5. `ResponseSink` 协议只保留两个 `on_*`，`dispatch` 下沉到 manager。  
6. 统一 lifecycle 时序；drop 后 `send_payload` 直接 no-op / 抛 `BridgeClosedError` 类错误。

---

### 3.2 Flow Control + Delivery + Protocol — ★★★★☆

**优点**

- **461B 实证预算**写进注释与配置；`chunk_tellraw` / `chunk_scriptevent` 用真实 `commandLine` UTF-8 字节探测，而非字符估算。  
- **语义分片 + 字符兜底**两层；`chunk_raw_command` 明确不可切，超限 `FrameTooLargeError`。  
- **`chunk_framed_scriptevent` 收敛保护**：`max_iterations = 10`，失败抛 `ProtocolError`（旧 review 无限循环风险已修）。  
- **配置校验扎实**：`FlowControlSettings` frozen + slots、`chunk_delays` 仅允许 `tellraw|scriptevent|text_resp`，并包成 `MappingProxyType`。  
- **Delivery 职责单一**：分片、节流、raw 日志；`OutboundText` / `SystemNotification` 有统一路由方法。

**问题**

| 严重度 | 位置 | 问题 | 失败场景 |
|--------|------|------|----------|
| **高** | `flow_control.py:290,321` 等 | 包装开销溢出 / 字节兜底失败抛裸 **`ValueError`**，不进公共异常树 | 超长玩家名 / 巨大 target 时宿主只 catch `FrameTooLargeError` 会漏掉；实测 `"名"*200` 即触发 |
| **中** | `protocol/minecraft.py:54-55` | `sanitize_tellraw_text` **死代码**；`create_tellraw` 不调用；`%` 未转义 | 内容含 `100%` / `%s` 时 Bedrock `rawtext` 可能当占位符，显示错乱 |
| **中** | `profiles/.../delivery.py` vs `FlowControlSettings` | `profile.response_chunk_delay` **从未读取**；实际延迟来自 `chunk_delays["text_resp"]` | 宿主改 profile 延迟以为生效，inter-chunk 仍 0.15s |
| **中** | `delivery/outbound.py:12` | **层倒置**：delivery 依赖 `gateway.messages` | 只想用底层分片/投递的宿主被迫牵入 gateway 值对象 |
| **中** | `codec.encode_bridge_request` | **出站 bridge 请求不分片**；超 461B → `FrameTooLargeError` | 大 capability payload 硬失败；与入站分片重组不对称 |
| **中** | `flow_control.py:312-316` | `_assert_byte_safe` JSON 解析失败时 **静默 return** | 畸形 payload 绕过字节兜底 |
| **中** | Profile 可扩展性 | codec/session **硬编码** mcbews_v1 分片语法；profile 只换字符串常量 | 第二套 wire 协议无法仅靠 settings 插入 |
| **低** | emoji / ZWJ | 按 codepoint 切分可能截断组合字符 | 单片 tellraw 显示残缺 emoji（整包重装后数据不丢） |
| **低** | `chunk_delay_for` 未知 kind | 静默返回 `0.0` | typo 如 `"text-resp"` → 全速连发，客户端可能丢包 |
| **低** | `SystemNotification` 颜色硬编码 | delivery 内写死 § 色 | 宿主难主题化 |

**改进建议**

1. **P0**：流控/包装溢出统一 `FrameTooLargeError` / `ProtocolError`；补巨大 target/player 测试。  
2. **P0**：决定 `%` 策略——启用（修正后的）sanitize，或删除死函数并文档化透传。  
3. 统一 `text_resp` 延迟归属：要么 profile 字段驱动，要么删字段只保留 flow settings。  
4. 打破 `delivery → gateway` 依赖（消息 VO 上移中立包，或 convenience 方法上移 sink）。  
5. 文档化 bridge 出站 461B 硬限，或做与入站对称的请求分片（需 addon 协同）。  
6. `_assert_byte_safe` 失败应 raise；未知 delay kind 应 warning。

---

### 3.3 Python Addon Bridge — ★★★★☆

**优点**

- **每连接 `AddonBridgeSession`**，disconnect 时 `close()` 将 pending future 置为 `BridgeClosedError`。  
- **分片重组防御面完整**：index/total 校验、重复块内容变更检测、total 变更检测、单消息字节上限、总 buffer 上限、pending 上限、buffer id 上限、TTL 过期（在 buffer 接近上限时 prune）。  
- **超时路径**：`wait_for` → `BridgeTimeoutError(request_id)`，`finally` 清理 pending。  
- **错误层次清晰**：`BridgeTimeoutError` / `BridgeClosedError` / `BridgeLimitError` 带上下文。  
- **协议常量与 TS 对齐**（`mcbews:bridge_req` / `MCBEWS|BRIDGE` / `MCBEWS_BRIDGE` 等）。

**问题**

| 严重度 | 位置 | 问题 | 失败场景 |
|--------|------|------|----------|
| **中** | `session.py:238-242` | TTL 仅在 buffer 数 ≥ 75% `max_buffer_ids` 时 prune | 少量但长期挂起的半包 buffer 可存活远超 TTL，占内存直到下次高压 |
| **中** | `service.py:151-157` | bridge 回包时若 session 不存在：`handled=True` 静默丢弃 | 竞态：请求超时已 cancel session 后迟到的 chunk；或错误 connection_id → 无错误可观测（仅 warning） |
| **低** | `service.py:91-98` | `bridge_request_outbound` 以 INFO 打出完整 `payload`/`command` | 生产日志可能泄露玩家坐标、背包等敏感能力数据 |
| **低** | UI vs Bridge session 创建时机 | UI chat 用 `_session_for` 懒创建；bridge 响应用 `get` 不创建 | 行为不对称，合理但需文档说明 |

**改进建议**

1. 后台周期 prune，或每次 `handle_chat_chunk` 检查**当前** buffer TTL，不依赖全局水位。  
2. 无 session 的 bridge chunk 记 metrics / 结构化 counter，便于诊断超时后的迟到包。  
3. INFO 日志默认只打 request_id/capability/size，payload 放 DEBUG 或 opt-in。

---

### 3.4 TypeScript Addon — ★★★☆☆

**优点**

- **模块拆分清楚**：`router` / `chunking` / `responseSync` / `capabilities/*` / `toolPlayer`。  
- **请求解析严格**：JSON 畸形、版本不支持、能力不支持均有结构化错误码。  
- **预就绪队列**（`MAX_PRE_READY_REQUESTS = 64`）避免 bridge 未 ready 丢包。  
- **串行处理尾**（`processingTail`）降低 Script API 并发踩坑。  
- **上下行分片配额文档化**（上行 256 字符 / 下行 400）。  
- **能力 denylist 基础防护**：`stop/reload/kick/op/deop`。  
- **测试量可观**（约 1.4k LOC，覆盖 router/chunking/capabilities/manifest）。

**问题**

| 严重度 | 位置 | 问题 | 失败场景 |
|--------|------|------|----------|
| **高** | `commandSafety.ts:1` + `runWorldCommand.ts` | denylist 过窄；`execute` / `script` / `gamemode` / `setblock` / 函数调用等未拦 | 任意 bridge 调用方可在 overworld 执行大量破坏性命令；SDK 信任边界在「谁能连 WS」 |
| **中** | `router.ts:53-58` | 模块级可变单例（`isBridgeRouterRegistered` / `capabilityHandler` / …） | Python 侧刻意去单例；Addon 仍是进程级全局，热重载 / 双初始化脆弱 |
| **中** | 安全模型 | 无鉴权、无 capability ACL、无 per-player 授权 | 一旦 WS 暴露到不可信网络，bridge = 远程命令面 |
| **低** | 与 Python Profile | 常量硬编码在 `constants.ts`，无 codegen / 共享 schema | 改 wire 常量需双端人工同步（有 `check_protocol_names` 部分缓解） |

**改进建议**

1. 将 `runWorldCommand` 默认改为 **opt-in** 或默认 deny-all + 允许列表。  
2. 扩展 denylist / 增加 allowlist 模式；文档明确「bridge 不是安全边界，宿主必须鉴权」。  
3. 长期：TS 常量与 Python profile 由单一 source-of-truth 生成。

---

### 3.5 配置 / 公共 API / 错误 — ★★★★★

- `GatewaySettings` / `FlowControlSettings` / `AddonBridgeSettings` / `WebsocketTransportConfig` 均为 frozen + slots + `__post_init__` 校验。  
- `CommandRegistry` 的 `aliases` 已是 `tuple`，`add_alias` 用 `dataclasses.replace`（旧 review 可变 list 问题**已修**）。  
- 异常层次清晰、可 catch 粒度合理。  
- `__all__` + `test_public_api.py` 快照防止 API  silently 膨胀。  
- `py.typed` + hatch `force-include` 正确。

**小建议**：`CommandRegistry.resolve_parsed` 按 dict 插入序匹配前缀；重叠前缀（`#a` vs `#ab`）依赖注册顺序。可文档约定「最长匹配」或测试固定该行为。

---

### 3.6 测试 / CI / 打包 / 文档 — ★★★★☆

**Scorecard**

| 项 | 状态 | 证据 |
|----|------|------|
| 单元测试覆盖模块 | ✅ 高 | 22 个 Python 测试文件；gateway/flow/addon/delivery 均有对应 |
| 测试/源码比 | ✅ | Python ~3.1k test / 3.4k src |
| Facade 不绑端口驱动 | ✅ | FakeWebSocket + RecordingHook/Sink（`test_server_facade.py` ~784 行） |
| 公共 API 快照 | ✅ | `test_public_api.py` |
| 示例 smoke | ✅ | `tests/smoke/test_examples.py` + 3 个 example |
| Release 校验 | ✅ | dist / release_tag / workflows 测试 + `tools/check_*` |
| CI Python 矩阵 | ✅ | 3.11–3.14 |
| CI websockets 矩阵 | ✅ | 12 / 14 / 16 |
| CI addon | ✅ | test + lint + typecheck + production build |
| CI docs | ✅ | `mkdocs build --strict` |
| Coverage 门槛 | ❌ | PRD 要求核心 ≥85%，**无 pytest-cov、无 CI 阈值** |
| 真机 / 集成 e2e | ❌ | 无真实 MCBE `/wsserver` 联调流水线 |
| 示例数量 | ✅ 改善 | basic-server / addon-server / addon-capability-call（旧 review 写「仅 1 个」已过时） |
| API 文档 | ✅ 改善 | Material + mkdocstrings + api-autonav（旧 review「无」已过时） |

**文档漂移（需修）**

| 文档 | 代码 | 说明 |
|------|------|------|
| PRD：`PlayerSession` 按玩家隔离 | `ConnectionState` **无** player session（测试甚至断言无 `_player_sessions`） | 隔离正确地下沉到宿主；PRD 应改写为「SDK 传递 sender，宿主分桶」 |
| PRD：测试覆盖 ≥85% | CI 不测覆盖率 | 目标未工程化 |
| 旧 architecture review 多项问题 | 源码已修 | Profile Protocol、aliases tuple、EventBus 隔离、chunk 迭代上限、chunk_delays 校验、DeprecationWarning 等 |

---

## 4. 完成度对照 PRD

| PRD 能力 | 完成度 | 备注 |
|----------|--------|------|
| 协议 Pydantic 模型 | ✅ | `protocol/minecraft.py` + profile models |
| 流控 4 类分片 | ✅ | tellraw / scriptevent / framed / raw |
| CommandRegistry | ✅ | 整词匹配 + 别名 |
| McbeOutboundDelivery | ✅ | |
| EventBus + 全量 WsEventType | ✅ | |
| ConnectionHook 6 点 | ⚠️ | API 在；`on_player_message` 返回值未用 |
| ResponseSink 2 路 | ✅ | |
| McbeServerFacade + run_lifetime | ✅ | |
| Addon 桥 request/response | ✅ | 无全局单例 |
| McbewsV1Profile | ✅ | Protocol + 单例 `MCBEWS_V1` |
| 多人 `(connection_id, player_name)` 分桶 | ⚠️ 宿主职责 | SDK 不提供 PlayerSession（合理，但 PRD 未更新） |
| 非目标（LLM/JWT/MessageBroker） | ✅ 守住 | 边界干净 |
| 依赖最小闭包 | ✅ | pydantic / websockets / structlog |
| mypy strict / ruff | ✅ | CI quality job |
| 核心覆盖 ≥85% | ❌ 未度量 | |

**结论**：功能完成度约 **90%**；剩余主要是契约闭合、PRD 对齐、安全默认值与覆盖率工程化。

---

## 5. 优先改进路线图

### P0 — 正确性 / 契约 / 安全（建议下一迭代必做）

1. **隔离 hook 与帧校验异常**（禁止单消息拆连接）  
2. **闭合 `on_player_message` 契约**（实现 bool 语义或改为 `-> None`）  
3. **有界 `response_queue` + disconnect 丢弃策略**  
4. **Addon `runWorldCommand` 收紧**（allowlist / opt-in；文档写明信任边界）  
5. **更新 PRD**（删除 SDK 内 `PlayerSession` 承诺，或明确宿主实现）  

### P1 — 鲁棒性与 API 诚实

6. 停止丢弃 `parse_typed_command`；事件/hook 携带 `ParsedCommand \| None`  
7. `ResponseSink` 去掉强制 `dispatch`  
8. 对齐 bus/hook lifecycle 时序；drop 后禁止 `send_payload`  
9. 流控异常统一进 `FrameTooLargeError`/`ProtocolError`；处理 `%` sanitize 死代码  
10. 统一 `response_chunk_delay` vs `chunk_delays["text_resp"]`  
11. Bridge buffer TTL：每次 accept 检查当前 buffer，或周期 prune  
12. Bridge INFO 日志脱敏；response-sender 去掉 0.5s 轮询  
13. 打破 `delivery → gateway` 层倒置  

### P2 — 工程硬化与 DX

14. `pytest-cov` + CI 核心包 ≥85%（对齐 PRD）  
15. 扩展 flow 边界矩阵测试（empty / CJK / emoji / 自定义 budget / framed 位数跨越）  
16. 可选：录制式 WS fixture / 真机 e2e  
17. TS/Python wire 常量单一源；profile 真策略化或文档降级为「常量袋」  
18. `McbeOutboundDelivery.send_chunked` 可注入 sleeper  

### 不必急着做

- 在 SDK 内重建 `PlayerSession`（与「无宿主业务」冲突）  
- 内置 JWT/登录  
- 多 profile 运行时热切换（当前单 profile 足够）  

---

## 6. 与业界 SDK 对照（简表）

| 维度 | 本项目 | 评价 |
|------|--------|------|
| DI / 可替换协作方 | Protocol + 构造注入 | 对齐 httpx/ASGI 级实践 |
| 无全局状态（Python） | 全量实例化 | 优于 requests/boto3 默认全局 |
| 公共 API 管控 | `__all__` + 快照测试 | 纪律性强 |
| 类型安全 | mypy strict + py.typed | 达标 |
| 错误体系 | 分层 + request 字段 | 达标 |
| 安全默认值（addon 命令） | denylist 偏薄 | 落后于「安全默认」原则 |
| 覆盖率门槛 | 无 | 落后于严肃开源 SDK |
| 文档站点 | mkdocs Material | 已达标 |

---

## 7. 总结

`mcbe-ws-sdk` 是一次**成功的子系统抽取**：边界清晰、依赖反转贯彻、流控与 bridge 有实证约束、CI 矩阵与打包工具链完整，已经具备 **v0.1 独立发布** 的骨架质量。

要跨到「让外部服主安心用」的发布质量，优先收口四件事：

1. **热路径异常隔离**（hook 抛错不得拆连接）  
2. **Hook/命令解析/队列背压契约说到做到**  
3. **Addon 命令能力默认安全**  
4. **PRD / 覆盖率 / 文档与代码对齐**

修完 P0 后，架构评分可稳定到 ★★★★★ 档；当前 ★★★★☆ 的扣分主要来自热路径韧性与契约悬空，而非分层错误。

---

## 附录 A — 旧报告已修复项（避免重复开工）

以下在 `2026-07-20-sdk-architecture-review.md` 中提出，**当前代码已解决**：

- `aliases.append` 修改 frozen list → 现为 `tuple` + `replace`  
- `AddonBridgeProfile` 仅为别名 → 现为 `Protocol`  
- `chunk_framed_scriptevent` 无迭代上限 → `max_iterations=10`  
- EventBus handler 异常中断后续 → per-handler try/except  
- `chunk_delays` 未知 key 静默 0 → 校验 + `ConfigurationError`  
- `assert isinstance` 在 sink → 改为 `TypeError`  
- `player_name` 易误用 → `DeprecationWarning`  
- `py.typed` 未 force-include → hatch 已配置  
- 示例过少 / 无 API 文档 → 现有 3 示例 + mkdocs  

## 附录 B — 关键文件索引

| 文件 | 角色 |
|------|------|
| `src/mcbe_ws_sdk/gateway/server_facade.py` | 生命周期与入站路由 |
| `src/mcbe_ws_sdk/gateway/connection.py` | 连接与 response-sender |
| `src/mcbe_ws_sdk/gateway/hook.py` / `sink.py` | 宿主扩展契约 |
| `src/mcbe_ws_sdk/flow/flow_control.py` | 461B 分片 |
| `src/mcbe_ws_sdk/addon/session.py` / `service.py` | Bridge 会话 |
| `src/mcbe_ws_sdk/profiles/mcbews_v1/` | Wire profile |
| `addon/scripts/bridge/` | MCBE 脚本端 |
| `tests/unit/test_server_facade.py` | 网关主路径测试 |
| `docs/PRD.md` | 产品需求（部分过期） |

# PRD — mcbe-ws-sdk

> 产品需求：把 MCBE AI Agent 项目的 WebSocket 子系统核心抽取为独立、可发布的 Python 包 `mcbe-ws-sdk`。
> 本页是内部产品文档，不进入公开文档站。当前实现合同以
> [`docs/protocol-v2.md`](protocol-v2.md) 与安装包内的 MCBEWS/2 清单为准；
> 下文中标注“历史”的 MCBEWS/1 内容只记录抽取阶段的背景，不代表当前实现。

## 1. 问题

`MCBE-AI-Agent` 项目把 **MCBE WebSocket 网关能力**（数据包编解码、字节安全分片、
连接状态机、addon 桥请求-响应、AI 响应分片协议）和 **业务逻辑**
（PydanticAI 聊天、LLM 模板切换、MCP、`MessageBroker`）混在 `services/websocket/` +
`models/` 里。

别的 MCBE 服主没法复用这些网关能力；每次业务改动都可能破坏协议层。

## 2. 目标

- 抽出 **通用 MCBE WS 网关** 作为独立、可发布的 Python 包。
- 提供**双层接口**：底层事件总线 + 高层钩子/sink 协议。
- **绝对不污染**主仓 `services/` / `models/` — 新包放独立文件夹，独立 git 仓库。
- 透传 `PlayerMessageEvent.sender`，由**宿主**按 `(connection_id, sender)` 分桶做多人隔离。
- 通过 MCBEWS/2 提供双向能力、通知、元数据与持续请求流。
- 连接完成协议协商后处理业务；当前使用 V2（MCBEWS/2），不兼容版本明确拒绝并关闭。

### 非目标（明确不做）

- 不实现任何 LLM / PydanticAI 调用。
- 不实现宿主业务会话、审批或文本响应 DTO；需要时由宿主通过通用 V2 方法实现。
- 不内置登录/JWT 认证（宿主注入）。
- 不重写 `core/queue.MessageBroker`。
- 不多玩家 AI 响应分片同步。

## 3. 目标用户

| 用户 | 怎么用 |
| --- | --- |
| 想搭 MCBE WS 机器人的服主 | 用 `McbeServer` 起网关，实现少量 hook/sink |
| 需要 addon 桥接的团队 | 用 `server.v2`（`V2Bridge`）发起能力调用、通知与流 |
| 需要事件驱动处理者 | 订阅 `EventBus`（`WsEventType`）或 `state.native_events` 按事件挂业务 |
| 需要游戏侧能力 | 构建配套 TypeScript 附加包并开启 Beta APIs |

## 4. 功能需求

### 协议层（protocol）

- MCBE 入/出站数据包 Pydantic 模型：`MinecraftHeader`、`MinecraftCommand`、
  `MinecraftSubscribe`、`PlayerMessageEvent`、`MCColor`、`MCPrefix`。
- MCBEWS/2 逻辑消息：request / response / notification / 流消息，字段、编号、
  JSON 规则、错误与限制由 `profiles/mcbews_v2/manifest.json` 定义。
- 原生 MCBE WS 的 `header.version` 与 `body.version` 保持 Bedrock 合同，
  不属于 MCBEWS/1 兼容层。

### 流控层（flow）

- `FlowControlMiddleware` 分片 tellraw / scriptevent / raw。
- 字节预算默认 461 UTF-8 字节（经验值），只能降低；真实 UTF-8 字节校验，非估算。
- 句子语义优先分片 + 字符/字节兜底。
- 分片节流延迟（tellraw / scriptevent）。
- MCBEWS/2 上行独立测量完整载体帧，不套用下行 461 字节预算。

### 命令层（command）

- `CommandRegistry` 聊天 → `ParsedCommand`，整词匹配（前缀后必须空白或结束），
  主前缀 + 别名，别名运行时增删。

### 投递层（delivery）

- `McbeOutboundDelivery` 串联流控分片、延迟与 raw 日志。

### 网关层（gateway）

- 唯一公开入口 `McbeServer`；始终使用受管理业务/观察队列，不再有调度模式开关。
- 连接生命周期：
  `WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED`，
  失败为 `REJECTED → CLOSED`。
- 协议协商前只处理协商、必要控制帧和已关联的原生 `commandResponse` / `error`；
  不发布业务 `CONNECTED`、不调用 `on_connected`、不执行玩家消息、不缓存业务重放。
- 协议协商失败以 `1008` 关闭，原因标识长度有界；旧端、无响应、无 addon 与混合旧帧均不能继续业务。
- 多人隔离是宿主职责：SDK 只透传 `PlayerMessageEvent.sender`；
  权威身份以 `sender` 为准，`ConnectionState.player_name` 只是弃用的最近发言者指针。
- 字节级出站经由 `FlowControlMiddleware`，不重复实现分片。
- **事件总线** `EventBus` + `WsEventType` 枚举，以及 `state.native_events`
  的观察/拦截订阅。
- **钩子协议** `ConnectionHook`（5 个生命周期钩子，全部 `-> None`）：
  `on_connected` / `on_disconnected` / `on_player_message(state, event, parsed=None)` /
  `on_command_response` / `on_error`。`parsed` 是 registry 预解析结果，不是“已消费”布尔。
- **响应上抛** `ResponseSink`（2 路）：`on_outbound_text` / `on_system_notification`。
- `McbeServer.run_lifetime()` / `stop()`：宿主注入 hook/sink/registry；
  服务负责握手、订阅与协议协商，welcome 由宿主 `on_connected` 负责。

### MCBEWS/2 桥（profiles.mcbews_v2）

- `V2Bridge`：每连接协议协商、能力注册表、流注册表、请求/通知/流入口。
- `V2Endpoint`：已就绪代次、回复配对、顺序组、请求与流限制。
- 能力处理器接收 `(name, payload, CapabilityContext)`；未知方法、参数错误与处理器异常
  映射为 `UNSUPPORTED_METHOD` / `INVALID_PARAMS` / `HANDLER_FAILED`。
- 通知没有回复等待项；请求流要求协商出 `stream/1`，有界缓存，慢消费者 `LIMIT_EXCEEDED`。
- 旧 `mcbews:bridge_req` / `session_req` / `text_resp` 只记录拒绝，
  不产生业务响应或副作用。

### 附加包（addon）

- 只接受 `mcbews:negotiate_v2` 与 `mcbews:packet_v2`。
- 能力通过 `registerCapability` 按名注册；流通过 `registerV2Stream` 注册。
- 默认包注册 `get_player_snapshot`、`get_inventory_snapshot`、`demo.echo`、
  `demo.ticks` 与 `run_world_command`；`--base` 精简包只含两个只读快照。
- `run_world_command` 默认应用命令禁止列表；需要更严格时可传入显式允许列表。
- 依赖 `@minecraft/server` 2.8.0、`@minecraft/server-ui` 2.1.0，
  以及与 1.26.30 基线匹配的 gametest beta；两个包清单最低引擎为 `[1,26,30]`。
- 目标世界必须开启 **实验 → 测试版 API（Beta APIs）**。

### 示例交互合同

- 示例只处理去掉首尾空白后以 ASCII `!` 开头的玩家消息。
- 空串、空白、普通聊天、裸 `help` / `帮助`、句中 `!`、全角 `！` 静默，
  零回复与零能力/流调用。
- 裸 `!` 返回一次用法提示；未知 `!xxx` 返回一次提示；带前缀中文别名保留。
- 过滤只影响示例业务入口，不影响 SDK 协议帧与原生事件。
- 玩家错误展示包含方法/能力名、错误码、最多 256 UTF-8 字节的可读说明和行动提示；
  内部请求编号与堆栈留在日志。

## 5. 非功能需求

- **类型安全**：Pydantic v2 模型，mypy strict 全量通过。
- **异步原生**：asyncio 架构，async for WS 消息循环。
- **结构化日志**：structlog；默认摘要，完整 payload 仅显式开启。
- **测试覆盖**：核心层 line coverage ≥85%（`pyproject.toml` `fail_under = 85`）。
- **依赖最小闭包**：仅 `pydantic>=2`、`websockets>=12`、`structlog>=24`；
  不拖 httpx/PyJWT/pydantic-ai 进包。
- **Python 3.11+**。

## 6. 依赖（抽取阶段映射，历史）

下表记录最初从主仓搬迁的路径，部分模块已在 V2-only 重构中删除。

| 原位置 | 抽取后位置 | 当前状态 |
| --- | --- | --- |
| `models/minecraft.py` | `src/mcbe_ws_sdk/protocol/minecraft.py` | 保留 |
| `models/agent.py`（MCColor/MCPrefix） | 同上 | 保留 |
| `models/addon_bridge.py` | `src/mcbe_ws_sdk/protocol/addon.py` | 历史；旧桥模型已删除 |
| `services/addon/protocol.py` | `src/mcbe_ws_sdk/addon/protocol.py` | 历史；旧桥已删除 |
| `services/addon/session.py` | `src/mcbe_ws_sdk/addon/session.py` | 历史；旧桥已删除 |
| `services/addon/service.py` | `src/mcbe_ws_sdk/addon/service.py` | 历史；旧桥已删除 |
| `services/websocket/flow_control.py` | `src/mcbe_ws_sdk/flow/flow_control.py` | 保留 |
| `services/websocket/command.py` | `src/mcbe_ws_sdk/command/registry.py` | 保留 |
| `services/websocket/delivery.py` | `src/mcbe_ws_sdk/delivery/outbound.py` | 保留 |
| `services/websocket/connection.py` | `src/mcbe_ws_sdk/gateway/connection.py` | 保留并加入协议协商状态 |
| `services/websocket/minecraft.py` | `src/mcbe_ws_sdk/gateway/handler.py` | 保留 |

## 7. 历史迁移批序

- **批 A**（低风险纯搬迁）：协议模型 + flow + command + delivery + addon 三件套。
- **批 B**（事件体系）：events / hook / sink / config。
- **批 C**（核心重构）：connection 重写 + handler 抽净。
- **批 D**（门面+能力）：facade + service 改造。
- **批 E**（主仓适配+示例+文档）。

后续 V2-only 重构删除了批 D 的旧 facade/addon service 兼容层，
并新增 `profiles.mcbews_v2` 的协议协商、能力、通知与流实现。

## 8. 风险

| 风险 | 缓解 |
| --- | --- |
| 事件总线分配开销 | weakref 订阅 + 直接调用派发，benchmark 后迭代 |
| 协议升级字段 | 清单与测试向量固定字段；未知核心字段拒绝，未知可选元数据保留 |
| 旧端升级中断 | 迁移指南给出完整版本回退步骤；新版不内置 v1 降级 |
| 未完成实机验证 | 文档区分自动化覆盖与实机验收，不扩大支持承诺 |

## 9. 成功指标

- 包内 e2e（内存 WS 跑 `run_lifetime` 全链路），覆盖 MCBEWS/2 协议协商。
- 基础与完整演示附加包均可构建，能力清单可重复验证。
- 旧协议帧只产生拒绝，不产生业务副作用。
- `examples/basic-server` 与 `examples/addon-server` 端到端可用。
- mypy strict + ruff + pytest 全绿；CI hard gate：核心层 line coverage ≥85%。
- Python 3.11–3.14 与 websockets 12/14/16 矩阵通过。

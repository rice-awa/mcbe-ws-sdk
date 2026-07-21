# PRD — mcbe-ws-sdk

> 产品需求：把 MCBE AI Agent 项目的 WebSocket 子系统核心抽取为独立 Python 包 `mcbe-ws-sdk`（PyPI 发布）。
> 详细技术规格见 [`docs/spec/2026-07-18-ws-sdk-design.md`](spec/2026-07-18-ws-sdk-design.md)。

## 1. 问题

`MCBE-AI-Agent` 项目把 **MCBE WebSocket 网关能力**（数据包编解码、字节安全分片、连接/玩家状态机、addon 桥请求-响应会话、AI 响应分片协议）和 **业务逻辑**（PydanticAI 聊天、LLM 模板切换、MCP、`MessageBroker`）混在 `services/websocket/` + `models/` 里。

别的 MCBE 服主没法复用这些网关能力；每次业务改动都可能破坏协议层。

## 2. 目标

- 抽出 **通用 MCBE WS 网关** 作为独立、可发布的 Python 包。
- 提供**双层接口**：底层事件总线 + 高层钩子/sink 协议。
- **绝对不污染**主仓 `services/` / `models/` — 新包放独立文件夹 `mcbe-ws-sdk/`，独立 git 仓库。
- 兼容现有行为：SDK 透传 `PlayerMessageEvent.sender`，由**宿主**按 `(connection_id, sender)` 分桶做多人会话隔离；addon 桥 capability 请求-响应生命周期。

### 非目标（明确不做）

- 不实现任何 LLM / PydanticAI 调用
- 不包含入站能力注册表 — addon 端拥有所有能力处理逻辑
- 不内置登录/JWT 认证（宿主注入）
- 不重写 `core/queue.MessageBroker`
- 不多玩家 AI 响应分片同步

## 3. 目标用户

| 用户 | 怎么用 |
|---|---|
| 想搭 MCBE WS 机器人的服主 | 用 `McbeServerFacade` 起网关，注册少量 hook/sink |
| 需要 addon 桥接的团队 | 用 `AddonBridgeService` + `AddonBridgeClient` 发送能力请求 |
| 需要事件驱动处理者 | 订阅 `EventBus`（WsEventType）按事件挂业务 |

## 4. 功能需求

### 协议层（protocol）
- MCBE 入/出站数据包 Pydantic 模型：`MinecraftHeader`、`MinecraftCommand`、`MinecraftSubscribe`、`PlayerMessageEvent`、`MCColor`、`MCPrefix`
- addon 桥数据模型：`AddonBridgeRequest`、`BridgeChatChunk`、`UiChatChunk`

### 流控层（flow）
- `FlowControlMiddleware` 4 类分片：tellraw / scriptevent / text_resp / raw（后者超长抛 ValueError）
- 字节预算 461B（实测 462B 失败），真实 UTF-8 字节反推（非估算）
- 句子语义优先分片（`_SENTENCE_DELIMITER_RE`）+ 字符/字节兜底
- 分片节流延迟（tellraw 0.05 / scriptevent 0.05 / text_resp 0.15；文本响应 prelude 0.5 由 profile 控制）

### 命令层（command）
- `CommandRegistry` 聊天→`ParsedCommand`，整词匹配（前缀后必须空白或结束），主前缀+别名，别名运行时增删

### 投递层（delivery）
- `McbeOutboundDelivery` 串联流控分片+延迟+raw 日志

### 网关层（gateway）
- 连接状态机：`ConnectionState` / `ConnectionManager`（无 SDK 内 `PlayerSession`）
- 多人隔离：**宿主职责** — SDK 只透传 `PlayerMessageEvent.sender`；宿主按 `(connection_id, sender)` 分桶历史 / 锁 / 上下文。`ConnectionState.player_name` 仅为弃用的"最近发言者"便捷指针，权威身份以 `sender` 为准
- 字节级出站经由 `FlowControlMiddleware`，不重复实现分片
- **事件总线** `EventBus` + `WsEventType` 枚举：CONNECTED / DISCONNECTED / PLAYER_MESSAGE / BRIDGE_CHUNK / UI_CHAT_CHUNK / UI_CHAT_REASSEMBLED / COMMAND_RESPONSE / RAW_INBOUND / RAW_OUTBOUND
- **钩子协议** `ConnectionHook`（6 个钩子点，全部 `-> None`）：on_connected / on_disconnected / `on_player_message(state, event, parsed=None) -> None` / on_ui_chat_reassembled / on_command_response / on_error。`parsed` 为 registry 预解析结果，非"已消费"布尔
- **响应上抛** `ResponseSink`（2 路）：on_outbound_text / on_system_notification（协议上无 `dispatch`）
- `McbeServerFacade` + `run_lifetime`：宿主注入 hook/sink/addon/registry；facade 仅 handshake + subscribe，welcome 由宿主 `on_connected` 负责

### addon 桥（addon）
- `BridgeCodec`：`encode_bridge_request` / `decode_bridge_chat_chunk` / `reassemble_bridge_chunks` / `decode_ui_chat_chunk` / `reassemble_ui_chat_chunks`
- `AddonBridgeSession`：分片缓存 + request future + 5s 超时
- `AddonBridgeService` / `AddonBridgeClient`：请求-响应生命周期（**无全局单例**）
- **信任边界**：bridge **不是**安全边界；宿主必须鉴权谁可调用能力。addon 仅对 `run_world_command` 做防御性 allow/denylist（详见 [`addon/README.md`](../addon/README.md#trust-boundary)）

### 协议 profile
- `McbewsV1Profile` — 唯一内置协议 profile，支持 mcbews v1 addon 互操作
- `MCBEWS_V1` — 模块级实例
- `encode_text_response_commands()` — 将文本响应编码为 `mcbews:text_resp` 命令列表
- `McbewsV1Delivery` — profile 特定的投递实现

## 5. 非功能需求

- **类型安全**：Pydantic v2 模型，mypy strict 全量通过
- **异步原生**：asyncio 架构，async for WS 消息循环
- **结构化日志**：structlog
- **测试覆盖**（NFR / CI 硬门禁）：核心层（flow / gateway / addon）line coverage ≥85%（`pyproject.toml` `fail_under = 85` + CI `--cov`）；覆盖率工具仅是开发/CI 依赖，不是打包/运行时依赖
- **依赖最小闭包**：仅 `pydantic>=2`、`websockets>=12`、`structlog>=24`；不拖 httpx/PyJWT/pydantic-ai 进包
- **Python 3.11+**

## 6. 依赖（从主仓搬迁源）

| 原位置 | 新位置 |
|---|---|
| `models/minecraft.py` | `src/mcbe_ws_sdk/protocol/minecraft.py` |
| `models/agent.py`（MCColor/MCPrefix） | 同上 |
| `models/addon_bridge.py` | `src/mcbe_ws_sdk/protocol/addon.py` |
| `services/addon/protocol.py` | `src/mcbe_ws_sdk/addon/protocol.py` |
| `services/addon/session.py` | `src/mcbe_ws_sdk/addon/session.py` |
| `services/addon/service.py` | `src/mcbe_ws_sdk/addon/service.py` |
| `services/websocket/flow_control.py` | `src/mcbe_ws_sdk/flow/flow_control.py` |
| `services/websocket/command.py` | `src/mcbe_ws_sdk/command/registry.py` |
| `services/websocket/delivery.py` | `src/mcbe_ws_sdk/delivery/outbound.py` |
| `services/websocket/connection.py` | `src/mcbe_ws_sdk/gateway/connection.py` |
| `services/websocket/minecraft.py` | `src/mcbe_ws_sdk/gateway/handler.py` |

## 7. 迁移批序

- **批 A**（低风险纯搬迁）：协议模型 + flow + command + delivery + addon 三件套 + 测试同步迁
- **批 B**（新建事件体系）：events / hook / sink / config Settings
- **批 C**（核心重构）：connection 重写 + handler 抽净
- **批 D**（门面+能力）：server_facade + service 改造
- **批 E**（主仓适配+示例+文档）

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 事件总线分配开销 | weakref 订阅 + 直接调用派发，benchmark 后迭代 |
| 协议升级字段 | 模型保留额外字段；增不改删，版本化 |
| `FlowControlMiddleware` 实例化方式变更 | 一步到位 + `from_settings()` 工厂留兼容 |
| 主仓 editable install 漂移 | 发布期前 editable 联调验证，PyPI 发版后主仓切 pinned |

## 9. 成功指标

- 包内 e2e（内存 WS 跑 `run_lifetime` 全链路）
- `examples/addon-capability-call` 端到端可用
- mypy strict + ruff + pytest 全绿；CI hard gate：核心层 line coverage ≥85%（非打包/运行时依赖）
- PyPI 发版（MIT）

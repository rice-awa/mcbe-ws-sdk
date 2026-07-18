# PRD — mcbe-ws-sdk

> 产品需求：把 MCBE AI Agent 项目的 WebSocket 子系统核心抽取为独立 Python 包 `mcbe-ws-sdk`（PyPI 发布）。
> 详细技术规格见 [`docs/spec/2026-07-18-ws-sdk-design.md`](spec/2026-07-18-ws-sdk-design.md)（遵循 superpowers brainstorming 流程产出）。

## 1. 问题

`MCBE-AI-Agent` 项目把 **MCBE WebSocket 网关能力**（数据包编解码、字节安全分片、连接/玩家状态机、addon 桥请求-响应会话、AI 响应分片协议）和 **业务逻辑**（PydanticAI 聊天、LLM 模板切换、MCP、`MessageBroker`）混在 `services/websocket/` + `models/` 里。

别的 MCBE 服主没法复用这些网关能力；每次业务改动都可能破坏协议层。

## 2. 目标

- 抽出 **通用 MCBE WS 网关** 作为独立、可发布的 Python 包。
- 提供**双层接口**：底层事件总线 + 高层能力 SDK。
- **绝对不污染**主仓 `services/` / `models/` — 新包放独立文件夹 `mcbe-ws-sdk/`，独立 git 仓库。
- 兼容现有行为：多人会话隔离 `(connection_id, player_name)`、AI 响应分片重组、addon 桥 capability/UI_CHAT 双链路。

### 非目标（明确不做）

- 不实现任何 LLM / PydanticAI 调用
- 不内置 `get_player_snapshot` 等具体能力 — 仅提供 `CapabilityRegistry` + 默认 `LoggingStub`，能力由宿主注册
- 不内置登录/JWT 认证（宿主注入）
- 不重写 `core/queue.MessageBroker`

## 3. 目标用户

| 用户 | 怎么用 |
|---|---|
| 想搭 MCBE WS 机器人的服主 | 用 `McbeServerFacade` 起网关，注册少量 handler |
| 需要 addon 桥接的团队 | 用 `AddonBridgeService` + `CapabilityRegistry` 暴露客户端能力 |
| 需要事件驱动处理者 | 订阅 `EventBus`（WsEventType）按事件挂业务 |

## 4. 功能需求

### 协议层（protocol）
- MCBE 入/出站数据包 Pydantic 模型：`MinecraftHeader`、`MinecraftCommand`、`MinecraftSubscribe`、`PlayerMessageEvent`、`MCColor`、`MCPrefix`
- addon 桥数据模型：`AddonBridgeRequest`、`BridgeChatChunk`、`UiChatChunk`

### 流控层（flow）
- `FlowControlMiddleware` 4 类分片：tellraw / scriptevent / ai_resp / raw（后者超长抛 ValueError）
- 字节预算 461B（实测 462B 失败），真实 UTF-8 字节反推（非估算）
- 句子语义优先分片（`_SENTENCE_DELIMITER_RE`）+ 字符/字节兜底
- 分片节流延迟（tellraw 0.05 / scriptevent 0.05 / ai_resp 0.15 / ai_resp_prelude 0.5）

### 命令层（command）
- `CommandRegistry` 聊天→`ParsedCommand`，整词匹配（前缀后必须空白或结束），主前缀+别名，别名运行时增删

### 投递层（delivery）
- `McbeOutboundDelivery` 串联流控分片+延迟+raw 日志

### 网关层（gateway）
- 连接状态机：`ConnectionState` / `PlayerSession`（按玩家隔离）/ `ConnectionManager`
- 多人隔离：主推 `(connection_id, player_name)` 分桶；连接级 `player_name` 仅作"最近发言者"便捷指针
- 字节级出站经由 `FlowControlMiddleware`，不重复实现分片
- **事件总线** `EventBus` + `WsEventType` 枚举：CONNECTED / DISCONNECTED / PLAYER_MESSAGE / BRIDGE_CHUNK / UI_CHAT_CHUNK / UI_CHAT_REASSEMBLED / COMMAND_RESPONSE / RAW_INBOUND / RAW_OUTBOUND
- **钩子协议** `ConnectionHook`（6 hook 点）：on_connected / on_authenticated / on_disconnected / on_player_message(→bool) / on_bridge_message(→bool) / on_ui_chat_reassembled / on_command_response
- **响应上抛** `ResponseSink`（5 路）+ `RouteEnvelope`：StreamChunk / SystemNotification / game_message / run_command / ai_response_sync
- `McbeServerFacade` + `run_lifetime`：宿主注入 hook/sink/addon/registry/broker

### addon 桥（addon）
- `BridgeCodec`：`encode_bridge_request` / `decode_bridge_chat_chunk` / `reassemble_bridge_chunks` / `decode_ui_chat_chunk` / `reassemble_ui_chat_chunks`
- `AddonBridgeSession`：分片缓存 + request future + 5s 超时
- `AddonBridgeService` / `AddonBridgeClient`：请求-响应生命周期（**删全局单例** `get_addon_bridge_service()`）

### 能力层（capability）
- `CapabilityHandler` / `CapabilityResult` / `CapabilityContext` / `CapabilityRegistry`
- 内置默认 `LoggingStubHandler`（仅日志 + 未实现哨兵），业务 handler 宿主注入

## 5. 非功能需求

- **类型安全**：Pydantic v2 模型，mypy strict 全量通过
- **异步原生**：asyncio 架构，async for WS 消息循环
- **结构化日志**：structlog
- **测试覆盖**：核心层（flow / gateway / addon）≥85%
- **依赖最小闭包**：仅 `pydantic>=2`、`pydantic-settings>=2`、`websockets>=12`、`structlog>=24`；不拖 httpx/PyJWT/pydantic-ai 进包
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

**留主仓不动**：`server.py`、`services/agent/tools.py`、`core/queue.py`、`core/session.py`、`config/`、`_version.py`

## 7. 迁移批序（计划文档详情见 PLAN.md）

- **批 A**（低风险纯搬迁）：协议模型 + flow + command + delivery + addon 三件套 + 测试同步迁
- **批 B**（新建事件体系）：events / hook / sink / config Settings
- **批 C**（核心重构）：connection 重写 + handler 抽净
- **批 D**（门面+能力）：server_facade + capability registry + service 改造
- **批 E**（主仓适配+示例+文档）：server.py 重写为 HostHook/HostSink/HostApp + examples + docs 6 篇

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 事件总线分配开销 | weakref 订阅 + 直接调用派发，benchmark 后迭代 |
| 协议升级（AI_RESP/bridge 字段） | 模型保留额外字段；增不改删，版本化 |
| 删 `get_addon_bridge_service()` 回归 | 必 grep 清理主仓全部引用，统一改引用包 |
| `FlowControlMiddleware` classmethod 改实例化 | 一步到位实例化 + `from_settings()` 工厂留兼容，旧的 deprecated |
| 主仓 editable install 漂移 | 发布期前 editable 联调验证，PyPI 发版后主仓切 pinned |

## 9. 成功指标

- 主仓 `pip install -e ./mcbe-ws-sdk` 后，现有 `tools_ws_tester.py` 回归全通过
- 包内 e2e（内存 WS 跑 `run_lifetime` 全链路）
- `examples/capability-greeting` + `examples/addon-ts` 端到端可用
- mypy strict + ruff + pytest 覆盖率（核心层 ≥85%）
- PyPI 发版（MIT）

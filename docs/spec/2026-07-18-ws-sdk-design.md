# WS-Subsystem Extraction — 技术规格

> Superpowers brainstorming 流程产出的规格文档。
> 日期：2026-07-18。对应产品需求 [`docs/PRD.md`](PRD.md)。

## 1. 概述

把 `MCBE-AI-Agent` 的 WS 子系统核心（数据包编解码、字节安全分片、连接/玩家状态机、addon 桥会话）抽成独立包 `mcbe-ws-sdk`，给 MCBE 服主提供通用 WebSocket 网关 SDK。

**关键约束**：
- 独立文件夹 `mcbe-ws-sdk/`，独立 git 仓库，**不污染主仓** `services/` / `models/`
- 抽出后兼容：多人会话隔离 `(connection_id, player_name)`、addon 桥 capability 请求-响应生命周期
- 双层接口：底层事件总线 + 高层钩子/sink 协议

## 2. 包边界（source of truth）

### 抽出部分（进入新包 `src/mcbe_ws_sdk/`）

| 层 | 模块 | 入口 |
|---|---|---|
| protocol | `minecraft.py`、`addon.py` | 纯 pydantic 协议模型 |
| flow | `flow_control.py` | `FlowControlMiddleware` |
| command | `registry.py` | `CommandRegistry`、`ParsedCommand` |
| delivery | `outbound.py` | `McbeOutboundDelivery` |
| gateway | `events.py`、`hook.py`、`sink.py`、`connection.py`、`handler.py`、`server_facade.py` | `McbeServerFacade`、`EventBus`、`ConnectionHook`、`ResponseSink` |
| addon | `protocol.py`、`session.py`、`service.py` | `AddonBridgeService`、`AddonBridgeClient`、`BridgeCodec` |
| profiles | `legacy_mcbeai_v1/` | `LegacyMcbeAiV1Profile`、`LEGACY_MCBEAI_V1`、`encode_legacy_response_commands`、`LegacyMcbeAiV1Delivery` |
| — | `config.py` | `FlowControlSettings`、`AddonBridgeSettings`、`GatewaySettings` |

### 留主仓不动

- `services/websocket/server.py`（应用命令分派）
- `services/agent/tools.py`（具体能力实现）
- `core/queue.py`（MessageBroker，依赖 pydantic_ai）
- `core/session.py`、`config/`、`services/auth/`、`_version.py`

## 3. 分层架构

```
┌─────────────────────────────────────────────────────────┐
│  宿主应用（主仓 server.py）                                │
│  HostHook / HostSink / HostApp                           │
└──────────────┬──────────────────────────────┬────────────┘
               │ 注入                             │ 注册
┌──────────────▼──────────────────────────────▼────────────┐
│  gateway (门面)                                           │
│  McbeServerFacade                                        │
│  ├─ connection 状态机                                     │
│  ├─ handler 协议处理                                      │
│  ├─ event bus (分发)                                     │
│  └─ hook + sink 协议                                     │
└──────────────┬──────────────────────────────┬────────────┘
               │                              │
┌──────────────▼──────────┐   ┌───────────────▼─────────────┐
│  addon 完整会话          │   │  flow + delivery            │
│  service / session /     │   │  字节反推分片 + 延迟         │
│  protocol (BridgeCodec)  │   └─────────────────────────────┘
└──────────────┬──────────┘
               │
┌──────────────▼──────────┐
│  protocol (纯模型)       │
│  minecraft / addon      │
│  pydantic               │
└─────────────────────────┘
```

## 4. 接口契约（设计冻结）

### 4.1 EventBus（gateway/events.py）

```python
class WsEventType(Enum):
    CONNECTED / DISCONNECTED / PLAYER_MESSAGE
    BRIDGE_CHUNK / UI_CHAT_CHUNK / UI_CHAT_REASSEMBLED
    COMMAND_RESPONSE / RAW_INBOUND / RAW_OUTBOUND
    ERROR

class EventBus:
    def subscribe(self, event: WsEventType, handler: Callable): ...
    async def emit(self, event: WsEventType, *args, **kwargs): ...
```

### 4.2 ConnectionHook（gateway/hook.py，Protocol）

```python
class ConnectionHook(Protocol):
    async def on_connected(self, state: ConnectionState): ...
    async def on_disconnected(self, state: ConnectionState): ...
    async def on_player_message(self, state, player_event) -> None: ...
    async def on_ui_chat_reassembled(self, state, player_name, message): ...
    async def on_command_response(self, state, request_id, response): ...
    async def on_error(self, state, error): ...
```

### 4.3 ResponseSink（gateway/sink.py，Protocol）

```python
class ResponseSink(Protocol):
    async def on_outbound_text(self, state, text: OutboundText): ...
    async def on_system_notification(self, state, note: SystemNotification): ...

class DefaultResponseSink:  # 包内提供
```

### 4.4 Settings 值对象（config.py）

```python
@dataclass(frozen=True)
class FlowControlSettings:
    command_line_byte_budget: int = 461
    max_chunk_content_length: int = 400
    chunk_sentence_mode: bool = True
    chunk_delays: dict[str, float] = ...

@dataclass(frozen=True)
class AddonBridgeSettings:
    timeout_seconds: float = 5.0
    protocol: AddonProtocolConfig = ...

@dataclass(frozen=True)
class GatewaySettings:
    flow: FlowControlSettings = ...
    addon: AddonBridgeSettings = ...
    websocket: WebsocketTransportConfig = ...
```

### 4.5 McbeServerFacade（gateway/server_facade.py）

```python
class McbeServerFacade:
    def __init__(
        self,
        *,
        settings: GatewaySettings | None = None,
        hook: ConnectionHook | None = None,
        sink: ResponseSink | None = None,
        addon: AddonBridgeService | None = None,
        registry: CommandRegistry | None = None,
    ): ...
    async def run_lifetime(self, host: str | None = None, port: int | None = None): ...
    async def stop(self): ...
```

### 4.6 AddonBridgeService（addon/service.py）

无全局单例；由宿主 new 实例注入 facade。

```python
class AddonBridgeService:
    def __init__(self, settings: AddonBridgeSettings): ...
    def create_client(self, connection_id, send_command) -> AddonBridgeClient: ...
    async def handle_player_message(self, connection_id, sender, message) -> AddonMessageResult: ...
    def is_bridge_chat_message(self, sender, message) -> bool: ...
    def is_ui_chat_message(self, sender, message) -> bool: ...
    def set_ui_chat_callback(self, callback): ...
    def close_connection(self, connection_id): ...
```

### 4.7 LegacyMcbeAiV1Profile（profiles/legacy_mcbeai_v1/）

唯一内置协议 profile，支持旧版 mcbeai v1 addon 桥接。

```python
@dataclass(frozen=True)
class LegacyMcbeAiV1Profile:
    bridge_request_message_id: str = "mcbeai:bridge_request"
    bridge_response_prefix: str = "MCBEAI|RESP"
    ui_chat_prefix: str = "MCBEAI|UI_CHAT"
    bridge_sender: str = "MCBEAI_TOOL"
    response_message_id: str = "mcbeai:ai_resp"
    request_version: int = 2
    response_chunk_delay: float = 0.15
    response_prelude_delay: float = 0.5

LEGACY_MCBEAI_V1 = LegacyMcbeAiV1Profile()
```

## 5. 关键重构（改造前/后对照）

### 5.1 connection.py if-elif → EventBus 派发

```python
# 改造前
if isinstance(msg, StreamChunk): ...
elif isinstance(msg, SystemNotification): ...

# 改造后
envelope = RouteEnvelope.from_message(msg)
await self.sink.dispatch(envelope)
```

### 5.2 全局 settings → 不可变值对象

`FlowControlMiddleware.__init__(self, settings: FlowControlSettings)` — 不再读 `get_settings()`。

### 5.3 `get_prompt_manager().clear_connection()` → hook 注入

`ConnectionManager.unregister` 删除 `get_prompt_manager().clear_connection(...)` 调用；宿主实现 `on_disconnected` 注入。

### 5.4 响应路由 → ResponseSink

DefaultResponseSink 处理默认路由；宿主实现 HostSink 覆盖出站行为。

### 5.5 `get_addon_bridge_service()` → 实例注入

删 `_addon_bridge_service` 全局；facade 构造时可选注入，为 None 则默认 `AddonBridgeService(AddonBridgeSettings())`。

### 5.6 入站能力注册表 → 移除

入站能力注册表已从 SDK 中移除。Addon 端拥有所有能力处理逻辑。SDK 仅通过 `AddonBridgeService` 发送请求并接收响应。

## 6. 依赖与打包

```
依赖（最小闭包）      python_requires
pydantic>=2.0         >=3.11
websockets>=12
structlog>=24.0
```

**不拖进包**：httpx、PyJWT、pydantic-ai（留主仓）。

工具链对齐主仓：ruff（line-length 100，target py311）/ mypy strict / pytest（核心层 ≥85%）/ twine check。

发布：PyPI `mcbe-ws-sdk`，MIT。

## 7. 验收 / 验证

- 包内 e2e：内存 WS 跑 `run_lifetime` 全链路
- 示例侧：`examples/addon-capability-call` 端到端
- 静态：ruff → mypy strict → pytest → twine check
- CI：workflow gates for quality / python matrix / websockets matrix / addon build / dist

## 8. 风险与权衡

| 风险 | 权衡 / 缓解 |
|---|---|
| 事件总线分配开销 | weakref 订阅 + 直接调用派发 |
| 协议升级字段 | 模型保留额外字段，增不改删 |
| `FlowControlMiddleware` 实例化方式变更 | 一步到位 + `from_settings()` 工厂留兼容 |
| 主仓 editable install 漂移 | 发布期前 editable 验证，发版后切 pinned |

## 9. 成功标准

- 全量验证通过（Python 3.11-3.14、websockets 12/14/16、addon build）
- 核心层覆盖率 ≥85%
- examples 端到端可用
- PyPI 发版

---

**设计审批**：本规格经用户口头批准（见对话上下文），进入实现计划阶段。

# PLAN — mcbe-ws-sdk 实现计划

> 基于冻结规格 [`docs/spec/2026-07-18-ws-sdk-design.md`](spec/2026-07-18-ws-sdk-design.md) 与产品需求 [`docs/PRD.md`](PRD.md)。
> 本文件对应 superpowers writing-plans 流程；按搬迁批序组织，低风险→高。

## 阶段总览

| 批 | 性质 | 主要工作 |
|---|---|---|
| A | 低风险纯搬迁 | 协议模型 + flow + command + delivery + addon 三件套 |
| B | 新建事件体系 | events / hook / sink / config Settings |
| C | 核心重构 | connection 重写 + handler 抽净 |
| D | 门面 + 能力 | server_facade + capability registry + service 改造 |
| E | 主仓适配 + 示例 + 文档 | HostHook/HostSink/HostApp + examples + docs 6 篇 |

**停点**：批 A–D 完成后即包里核心可用；批 E 主仓适配回归现有行为。

---

## 批 A — 低风险搬迁 + 最小改造

### A1. 协议层（protocol/）

**搬迁清单**：
- `models/minecraft.py` → `src/mcbe_ws_sdk/protocol/minecraft.py`
- `models/agent.py`（`MCColor` / `MCPrefix`）→ 同上
- `models/addon_bridge.py` → `src/mcbe_ws_sdk/protocol/addon.py`

**改造点**：
- 断内部 import（`from models.xxx` → `from mcbe_ws_sdk.protocol.xxx`）
- 去 `_version.py` 依赖（协议层零版本信息）

### A2. 流控层（flow/）

**搬迁**：`services/websocket/flow_control.py` → `src/mcbe_ws_sdk/flow/flow_control.py`

**改造**：
- `FlowControlMiddleware`：classmethod → 实例化 `__init__(self, settings: FlowControlSettings)`
- 删 `get_settings()` 全局依赖（在 `config.py` 提供 `FlowControlSettings` 后由主仓/门面注入）
- 仍保留字节反推分片核心逻辑不变

### A3. 命令层（command/）

**搬迁**：`services/websocket/command.py` → `src/mcbe_ws_sdk/command/registry.py`

**改造**：仅修 import 路径；命令注册语义（整词匹配、别名）保持不变。

### A4. 投递层（delivery/）

**搬迁**：`services/websocket/delivery.py` → `src/mcbe_ws_sdk/delivery/outbound.py`

**改造**：`McbeOutboundDelivery` 接受 `FlowControlSettings` 而非全局 settings；保持串联流控分片+延迟+raw 日志。

### A5. addon 桥（addon/）

**搬迁清单**：
- `services/addon/protocol.py` → `src/mcbe_ws_sdk/addon/protocol.py`（codec 加 protocol 参数，去 `_protocol()` 全局）
- `services/addon/session.py` → `src/mcbe_ws_sdk/addon/session.py`
- `services/addon/service.py` → `src/mcbe_ws_sdk/addon/service.py`（删 `_addon_bridge_service` 与 `get_addon_bridge_service()`；超时/protocol 走 `AddonBridgeSettings`）

### A6. 测试同步迁

- 把主仓 `tests/` 中涉及以上模块的测试迁到 `mcbe-ws-sdk/tests/unit/`
- 先保证包内单元测试独立可跑（`pytest mcbe-ws-sdk/tests`）

### 验证

- `pytest mcbe-ws-sdk/tests/unit` 全通过
- 主仓原有测试仍通过（未改主仓代码）

---

## 批 B — 新建事件体系

### B1. 配置值对象（config.py）

- `FlowControlSettings`（frozen dataclass，默认同主仓 flow_control 节）
- `AddonBridgeSettings`（timeout_seconds、protocol 默认值）
- `GatewaySettings`（组合 flow + addon + websocket transport 配置）

### B2. 事件总线（gateway/events.py）

- `WsEventType` 枚举（9 种事件）
- `EventBus`（subscribe / emit / 弱引用订阅避免泄漏）

### B3. 钩子协议（gateway/hook.py）

- `ConnectionHook` Protocol（6 hook 点，签名见 spec §4.2）
- `NoOpHook` 默认实现

### B4. 响应上抛（gateway/sink.py）

- `RouteEnvelope` + `ResponseKind` 枚举
- `ResponseSink` Protocol（5 路，签名见 spec §4.3）
- `DefaultResponseSink`（StreamChunk/SystemNotification 内置，后三路抛 NotImplementedError）

### 验证

- 新增 unit tests：事件派发、hook 可注入、sink 分流正确

---

## 批 C — 核心重构

### C1. connection.py → gateway/connection.py

**搬迁**：`services/websocket/connection.py` → `src/mcbe_ws_sdk/gateway/connection.py`

**重构**：
- 删 `from services.agent.prompt import get_prompt_manager`
- `unregister` 不再调 `get_prompt_manager().clear_connection(...)` → 由宿主 `on_disregistered` 注入
- 发送协程 `_response_sender`：if-elif → `RouteEnvelope.from_message(msg)` + `await self.sink.dispatch(envelope)`
- `ConnectionManager.register/unregister` 时 `bus.emit(CONNECTED/DISCONNECTED)`
- PlayerSession 的 `current_provider / current_template / custom_variables` 字段 → 通过 extension dict 外挂（宿主注入）；基础版本只保留 `context_enabled` 等核心字段或下沉到宿主 hook

### C2. minecraft.py → gateway/handler.py

**搬迁**：`services/websocket/minecraft.py` → `src/mcbe_ws_sdk/gateway/handler.py`

**重构**：
- 保留 `McbeProtocolHandler` 协议处理（construct subscribe / parse_player_message / create_welcome_message / create_*_message）
- 剥离 `create_chat_request`（产 `ChatRequest`，业务相关 → 宿主 hook 或留主仓）
- 改 import 路径依赖新包的 protocol + delivery + command

### 验证

- 连接状态机单元测试
- 发送协程 sink 分流 integration test

---

## 批 D — 门面 + 能力

### D1. server_facade.py

**新建**：`src/mcbe_ws_sdk/gateway/server_facade.py`

- `McbeServerFacade.__init__(settings, hook, sink, addon, registry, broker)` — 所有可选，为 None 给默认
- `run_lifetime(host, port)`：起 `websockets.serve` + ConnectionManager 主循环
- 不 import 业务（login/MCP/template 等）

### D2. capability/registry.py

**新建**：`src/mcbe_ws_sdk/capability/registry.py`

- `CapabilityContext` / `CapabilityHandler` / `CapabilityRegistry`
- 默认 `LoggingStubHandler`（日志 + 未实现哨兵）
- 注册/卸载/dispatch API

### D3. 重写 addon/service.py（完成批 A 开始的工作）

- 删全局单例；超时/protocol 由 `AddonBridgeSettings` 驱动

### 验证

- e2e：内存 WS 跑 `run_lifetime` 全链路（分片、addon 重组、能力调度）

---

## 批 E — 主仓适配 + 示例 + 文档

### E1. 主仓 server.py → HostHook/HostSink/HostApp

**重写**主仓 `services/websocket/server.py`：
- `HostHook(ConnectionHook)` 实现 6 hook 点（业务命令分派 on_player_message、login/MCP/template 等）
- `HostSink(ResponseSink)` 实现 5 路（game_message/run_command/ai_response_sync 走 MessageBroker）
- `HostApp` 注入 `McbeServerFacade`

### E2. 主仓 pyproject.toml

- 加 `mcbe-ws-sdk = {path = "./mcbe-ws-sdk", editable = true}`（或 `pip install -e ./mcbe-ws-sdk`）

### E3. 清理主仓对全局单例的引用

- 必 grep `get_addon_bridge_service()` / `models.agent.MCColor` / `services.agent.prompt.get_prompt_manager` in `services/` → 统一引用新包
- `services/agent/tools.py` 改引用新包

### E4. 示例

- `examples/addon-ts/`：`manifest.json` + `main.ts`（playerJoin → get_greeting capability + UI_CHAT 分片）
- `examples/capability-greeting/`：`greeting_handler.py`（paired Python 注册 get_greeting）

### E5. 文档 6 篇

- `docs/{quickstart,connection-hook,response-sink,capability-registry,protocol-reference,addon-example}.md`

### 验证

- `pip install -e ./mcbe-ws-sdk` 主仓 `tools_ws_tester.py` 回归
- `tests/test_integration_sdk.py`（主仓侧）
- examples 端到端可用

---

## 风险与应急预案

| 风险 | 预案 |
|---|---|
| 批 A 搬迁测试失败 | 主仓代码未动，最小时间回滚（删新包重做） |
| 批 C 重构破坏主仓 | 批 A/B 独立完成且测试通过，批 C 只动 connection 内部的发送协程 |
| editable install 依赖冲突 | 新包依赖最小闭包（pydantic/pydantic-settings/websockets/structlog），不引入主仓其他依赖 |
| 能力 hook 错漏 | ConnectionHook 返回 bool 保留"已消费"语义；NoOpHook 默认实现做兜底 |

## 验收标准（发布前必须达成）

- [ ] 包内 e2e：内存 WS 跑全链路通过
- [ ] 主仓 `tools_ws_tester.py` 回归通过
- [ ] `pytest mcbe-ws-sdk/tests` 覆盖率（核心层 ≥85%）
- [ ] `ruff` + `mypy strict` 全通过
- [ ] `twine check` 通过（可发布）
- [ ] examples 端到端可用

---

**下一步**：本计划经批准后进入执行（按批 A–E）。

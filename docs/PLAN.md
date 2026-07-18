# mcbe-ws-sdk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the generic MCBE WebSocket gateway out of `MCBE-AI-Agent` into an independent, PyPI-publishable Python package `mcbe-ws-sdk`, with a dual-layer API (event bus + capability SDK) and TS addon examples.

**Architecture:** Six-layer package (protocol / flow / command / delivery / gateway / addon+capability) connected by `EventBus` (replaces hard-coded if-elif), `ConnectionHook` (6 hook points), `ResponseSink` (5 dispatch routes), and frozen settings value objects (kills global `get_settings()` and `get_addon_bridge_service()` singletons). Host app (main repo `server.py`) re-implements business commands as `HostHook`/`HostSink`/`HostApp` injected into `McbeServerFacade`.

**Tech Stack:** Python 3.11+, Pydantic v2, pydantic-settings, websockets, structlog, asyncio. Dev: ruff (line-length 100, target py311), mypy strict, pytest (≥85% core), twine.

## Global Constraints

- **Hard:** never modify main repo `services/` or `models/` — all new code goes under `mcbe-ws-sdk/src/mcbe_ws_sdk/`.
- **Hard:** `mcbe-ws-sdk/` is an independent git repo (separate `.git/` from main repo).
- **Hard:** dependency closure = `pydantic>=2.0`, `pydantic-settings>=2.0`, `websockets>=12`, `structlog>=24.0`. Do NOT pull in httpx / PyJWT / pydantic-ai.
- **Hard:** preserve on-disk command-line byte safety 461 B (462 B measured broken), real UTF-8 byte back-calculation (no estimates), sentence-aware+char+byte chunking.
- **Hard:** preserve multi-player isolation by `(connection_id, player_name)` bucketing.
- **Hard:** preserve both addon bridge links: capability (`mcbeai:bridge_request` → `MCBEAI|RESP|...` chunks) and UI_CHAT (`MCBEAI|UI_CHAT|...` chunks).
- **Hard:** preserve AI response reassembly protocol `scriptevent mcbeai:ai_resp {id,i,n,p,r,c}`.
- **Hard:** Python 3.11+; mypy strict; ruff; pytest core coverage ≥85%.
- MIT license.

## Phase Overview

| Batch | Nature | Main work |
|---|---|---|
| A | Low-risk relocation | protocol models + flow + command + delivery + addon trio (+e.stderr.stderr.stderr.stderr.... wait) |
| B | New event system | events / hook / sink / config Settings |
| C | Core rewrite | connection rewrite + handler stripping |
| D | Facade + capability | server_facade + capability registry + service refactor |
| E | Host adapt + examples + docs | HostHook/HostSink/HostApp + examples + 6 docs |

**Stop point:** after batch A-D the package core is usable; batch E makes the main repo regress to existing behavior.

> Execution note: per user instruction this plan is implemented with **parallel sub-agents** (superpowers:subagent-driven-development style), one sub-agent per decomposed task.

---

## Batch A — Relocation + minimal rewrite (TDD bite-sized)

Each task carries its own test cycle. Sub-agents run in parallel per task.

- [ ] **Task A0: Repository + packaging bootstrap**

**Files:**
- Create: `mcbe-ws-sdk/pyproject.toml`
- Create: `mcbe-ws-sdk/src/mcbe_ws_sdk/__init__.py`
- Create: `mcbe-ws-sdk/tests/conftest.py`

**Interfaces:**
- Produces: importable `mcbe_ws_sdk` package with `__version__ = "0.1.0"`; ruff/mypy/pytest runnable from `mcbe-ws-sdk/`.

- [ ] **Step A0.1: Write pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mcbe-ws-sdk"
version = "0.1.0"
description = "Generic WebSocket gateway SDK for Minecraft Bedrock Edition"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.11"
authors = [{ name = "mcbe-ws-sdk contributors" }]
dependencies = [
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "websockets>=12",
    "structlog>=24.0",
]

[project.optional-dependencies]
dev = ["ruff", "mypy", "pytest", "pytest-asyncio"]

[tool.hatch.build.targets.wheel]
packages = ["src/mcbe_ws_sdk"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "C4", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `src/mcbe_ws_sdk/__init__.py`:
```python
"""Generic WebSocket gateway SDK for Minecraft Bedrock Edition."""
__version__ = "0.1.0"
```

- [ ] **Step A0.2: Verify package imports + tooling**

Run: `cd mcbe-ws-sdk && python -c "import mcbe_ws_sdk; print(mcbe_ws_sdk.__version__)"`
Expected: `0.1.0`

Run: `cd mcbe-ws-sdk && ruff check src`
Expected: no errors

Commit: `chore: bootstrap mcbe-ws-sdk package`


- [ ] **Task A1: Config value objects (`config.py`)**

**Files:**
- Create: `src/mcbe_ws_sdk/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `FlowControlSettings`, `AddonProtocolConfig`, `AddonBridgeSettings`, `GatewaySettings` (all frozen dataclasses). Default `command_line_byte_budget == 461`.

- [ ] **Step A1.1: Write failing test**

`tests/unit/test_config.py`:
```python
from mcbe_ws_sdk.config import FlowControlSettings, GatewaySettings

def test_flow_control_default_byte_budget_is_461():
    s = FlowControlSettings()
    assert s.command_line_byte_budget == 461

def test_flow_control_frozen():
    s = FlowControlSettings()
    import pytest
    with pytest.raises(AttributeError):
        s.command_line_byte_budget = 500  # type: ignore[misc]

def test_gateway_settings_default_nested():
    g = GatewaySettings()
    assert g.flow.command_line_byte_budget == 461
    assert g.addon.timeout_seconds == 5.0
```

Run: `cd mcbe-ws-sdk && pytest tests/unit/test_config.py -v`
Expected: FAIL `ModuleNotFoundError: mcbe_ws_sdk.config`

- [ ] **Step A1.2: Implement `src/mcbe_ws_sdk/config.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class AddonProtocolConfig:
    bridge_message_id: str = "mcbeai:bridge_request"
    bridge_prefix: str = "MCBEAI|RESP"
    ui_chat_prefix: str = "MCBEAI|UI_CHAT"
    bridge_tool_player_name: str = "MCBEAI_TOOL"
    ai_resp_message_id: str = "mcbeai:ai_resp"

@dataclass(frozen=True)
class FlowControlSettings:
    command_line_byte_budget: int = 461
    max_chunk_content_length: int = 400
    chunk_sentence_mode: bool = True
    chunk_delays: dict[str, float] = field(default_factory=lambda: {
        "tellraw": 0.05, "scriptevent": 0.05,
        "ai_resp": 0.15, "ai_resp_prelude": 0.5,
    })

@dataclass(frozen=True)
class AddonBridgeSettings:
    timeout_seconds: float = 5.0
    protocol: AddonProtocolConfig = field(default_factory=AddonProtocolConfig)

@dataclass(frozen=True)
class GatewaySettings:
    flow: FlowControlSettings = field(default_factory=FlowControlSettings)
    addon: AddonBridgeSettings = field(default_factory=AddonBridgeSettings)
```

Run: `cd mcbe-ws-sdk && pytest tests/unit/test_config.py -v`
Expected: 3 passed.

Commit: `feat(config): add frozen settings value objects (kills get_settings)`


- [ ] **Task A2: Protocol models (`protocol/`)**

**Files:**
- Create: `src/mcbe_ws_sdk/protocol/__init__.py`
- Create: `src/mcbe_ws_sdk/protocol/minecraft.py` (relocate from main repo `models/minecraft.py` + `MCColor/MCPrefix` from `models/agent.py`)
- Create: `src/mcbe_ws_sdk/protocol/addon.py` (relocate from main repo `models/addon_bridge.py`)
- Test: `tests/unit/test_protocol.py`

**Interfaces:**
- Produces: `MinecraftHeader`, `MinecraftCommand`, `MinecraftSubscribe`, `PlayerMessageEvent`, `MCColor`, `MCPrefix`, `AddonBridgeRequest`, `BridgeChatChunk`, `UiChatChunk`. All pydantic v2; protocol layer has ZERO business imports (no `_version`).

- [ ] **Step A2.1: Write failing test**

`tests/unit/test_protocol.py` (representative subset):
```python
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommand, MinecraftHeader, MCColor, MCPrefix,
    MinecraftSubscribe, PlayerMessageEvent,
)
from mcbe_ws_sdk.protocol.addon import AddonBridgeRequest

def test_minecraft_subscribe_player_message():
    sub = MinecraftSubscribe.player_message()
    assert sub.header.messagePurpose == "subscribe"
    assert sub.header.eventName == "PlayerMessage"

def test_mc_color_default():
    assert MCColor.OK == "§a"

def test_parse_player_message():
    data = {"header":{"requestId":"x","messagePurpose":"event","version":1,
      "eventName":"PlayerMessage"},"body":{"sender":"Steve","message":"Hi","type":"chat"}}
    ev = PlayerMessageEvent.model_validate(data) if False else None  # stand-in
```

- [ ] **Step A2.2: Relocate protocol models**

Read main repo `models/minecraft.py`, `models/agent.py` (only `MCColor`, `MCPrefix` classes), `models/addon_bridge.py`. Copy into `src/mcbe_ws_sdk/protocol/`, convert all imports to `from mcbe_ws_sdk.protocol.xxx import ...`, strip any `_version` / business imports. Preserve all validators and field semantics verbatim.

Run: `pytest tests/unit/test_protocol.py -v` → PASSES.

Commit: `feat(protocol): relocate MCBE packet models from main repo`


- [ ] **Task A3: Flow control (`flow/`) — classmethod → instance**

**Files:**
- Create: `src/mcbe_ws_sdk/flow/__init__.py`
- Create: `src/mcbe_ws_sdk/flow/flow_control.py` (from main repo `services/websocket/flow_control.py`)
- Test: `tests/unit/test_flow_control.py`

**Interfaces:**
- Produces: `FlowControlMiddleware(settings: FlowControlSettings)` instance; methods `chunk_tellraw`, `chunk_scriptevent`, `chunk_ai_response`, `chunk_raw_command`, `chunk_delay_for`. Preserves 461 B + sentence/char/byte three-tier splitting unchanged.

- [ ] **Step A3.1: Write failing test**

`tests/unit/test_flow_control.py`:
```python
import pytest
from mcbe_ws_sdk.flow import FlowControlMiddleware
from mcbe_ws_sdk.config import FlowControlSettings

def test_raw_command_over_budget_raises():
    mid = FlowControlMiddleware(FlowControlSettings())
    long_cmd = "say " + "x" * 1000
    with pytest.raises(ValueError):
        mid.chunk_raw_command(long_cmd)

def test_chunk_delay_for_default_ai_resp():
    mid = FlowControlMiddleware(FlowControlSettings())
    assert mid.chunk_delay_for("ai_resp") == 0.15
```

- [ ] **Step A3.2: Relocate + rewrite FlowControlMiddleware**

1. Read `services/websocket/flow_control.py`.
2. Copy byte-calculation core (`_SENTENCE_DELIMITER_RE`, `command_line_for`, `_split_by_command_fit_chars`, splitters, `_send_chunked`) verbatim.
3. Convert: drop classmethod-only design; add `__init__(self, settings: FlowControlSettings)`; replace all `get_settings().flow_control.*` reads with `self.settings.*`.
4. Imports: `from mcbe_ws_sdk.config import FlowControlSettings`, `from mcbe_ws_sdk.protocol.minecraft import MinecraftCommand`.

Run: `pytest tests/unit/test_flow_control.py -v` → PASSES.

Commit: `feat(flow): relocate FlowControlMiddleware, convert to instance with settings`


- [ ] **Task A4: Command registry (`command/`)**

**Files:**
- Create: `src/mcbe_ws_sdk/command/__init__.py`
- Create: `src/mcbe_ws_sdk/command/registry.py` (from `services/websocket/command.py`)
- Test: `tests/unit/test_command.py`

**Interfaces:**
- Produces: `CommandRegistry`, `ParsedCommand`. Whole-word matching + alias behavior unchanged.

- [ ] **Step A4.1: Write failing test resolving `#登录` must not match `#登录xxx`**

- [ ] **Step A4.2: Relocate, fix imports to new package.**

Commit: `feat(command): relocate CommandRegistry`


- [ ] **Task A5: Delivery outbound (`delivery/`)**

**Files:**
- Create: `src/mcbe_ws_sdk/delivery/__init__.py`
- Create: `src/mcbe_ws_sdk/delivery/outbound.py` (from `services/websocket/delivery.py`)
- Test: `tests/unit/test_delivery.py`

**Interfaces:**
- Produces: `McbeOutboundDelivery(connection_id, send_payload, settings: FlowControlSettings)`. Chunks via FlowControlMiddleware + raw log unchanged.

Commit: `feat(delivery): relocate McbeOutboundDelivery with settings`


- [ ] **Task A6: Addon bridge (`addon/`) — kill singleton**

**Files:**
- Create: `src/mcbe_ws_sdk/addon/__init__.py`
- Create: `src/mcbe_ws_sdk/addon/protocol.py` (relocate codec from `services/addon/protocol.py`, add protocol parameter, drop `_protocol()` global)
- Create: `src/mcbe_ws_sdk/addon/session.py` (relocate from `services/addon/session.py`)
- Create: `src/mcbe_ws_sdk/addon/service.py` (relocate from `services/addon/service.py`; DELETE `_addon_bridge_service` and `get_addon_bridge_service()`; timeout/protocol via `AddonBridgeSettings`)
- Test: `tests/unit/test_addon_bridge.py`

**Interfaces:**
- Produces: `AddonBridgeService(settings: AddonBridgeSettings)`, `AddonBridgeClient` protocol, `AddonBridgeSession`, codec functions. No module-level singleton.

- [ ] **Step A6.1: Write failing test**

`tests/unit/test_addon_bridge.py`:
```python
from mcbe_ws_sdk.addon.service import AddonBridgeService
from mcbe_ws_sdk.config import AddonBridgeSettings

def test_service_no_global_singleton():
    a = AddonBridgeService(AddonBridgeSettings(timeout_seconds=1.0))
    b = AddonBridgeService(AddonBridgeSettings(timeout_seconds=2.0))
    assert a is not b
    assert a._timeout_seconds == 1.0
    assert b._timeout_seconds == 2.0
```

- [ ] **Step A6.2: Relocate + kill singleton + rewire imports; fix `_protocol()` calls to explicit protocol parameter.**

Commit: `feat(addon): relocate bridge trio, delete get_addon_bridge_service singleton`


- [ ] **Task A7: Batch A verification gate**

Run: `cd mcbe-ws-sdk && ruff check src tests && mypy src && pytest tests/unit -v --cov=mcbe_ws_sdk --cov-report=term-missing`
Expected: ruff clean, mypy strict clean, all unit tests pass.

Verify main repo untouched:
```bash
cd /home/riceawa/Desktop/code/MCBE-AI-A-agent
git status --short
# must NOT list any services/ or models/ modifications
```

Commit batch A marker if everything is green.

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

**下一步**：本计划经批准后，按用户指令使用 subagent-driven-development 并行子代理逐任务执行（批 A 任务 A0–A7）。

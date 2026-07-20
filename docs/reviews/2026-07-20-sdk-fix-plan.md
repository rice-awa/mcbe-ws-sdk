# MCBE-WS-SDK 架构评估修复计划

> 来源：`docs/reviews/2026-07-20-sdk-architecture-review.md`  
> 日期：2026-07-20  
> 策略：单 PR 全部修复（#11 示例除外）

---

## 概览

| 类别 | 条目数 | 已采纳 | 跳过 |
|------|--------|--------|------|
| 4.1 正确性问题 | 3 | 3 | 0 |
| 4.2 设计改进 | 7 | 7 | 0 |
| 4.3 次要建议 | 7 | 6 | 1 (#11) |
| **合计** | **17** | **16** | **1** |

---

## 修复清单

### F-01 — Frozen dataclass 可变字段被 mutate

- **文件**：`src/mcbe_ws_sdk/command/registry.py`
- **问题**：`MinecraftCommandConfig` 是 frozen dataclass，但 `aliases: list[str]` 被 `add_alias()` 做 `append()`
- **修复**：
  1. `MinecraftCommandConfig.aliases` 类型从 `list[str]` 改为 `tuple[str, ...]`，`field(default_factory=tuple)`
  2. `add_alias()` 改为通过 `_commands[command_prefix] = replace(config, aliases=config.aliases + (alias,))` 重建不可变对象
  3. `remove_alias()` 同理，用 tuple 推导重建
  4. `get_aliases()` 返回类型从 `list[str]` 改为 `tuple[str, ...]`
  5. `list_all_commands()` 返回的别名部分改为 tuple
- **验证**：`tests/unit/test_command.py` 现有测试通过，确认无 `RuntimeError`

### F-02 — `dispatch()` 中的 assert 在 `-O` 下失效

- **文件**：`src/mcbe_ws_sdk/gateway/sink.py`
- **问题**：`DefaultResponseSink.dispatch()` 使用 `assert isinstance(...)` 做类型校验，`python -O` 下被移除
- **修复**：
  ```python
  async def dispatch(self, state: ConnectionState, envelope: RouteEnvelope) -> None:
      if envelope.kind is ResponseKind.OUTBOUND_TEXT:
          if not isinstance(envelope.payload, OutboundText):
              raise TypeError(...)
          await self.on_outbound_text(state, envelope.payload)
          return
      if not isinstance(envelope.payload, SystemNotification):
          raise TypeError(...)
      await self.on_system_notification(state, envelope.payload)
  ```
- **验证**：`tests/unit/test_sink.py` 现有测试通过

### F-03 — `ConnectionState.player_name` 不可靠

- **文件**：`src/mcbe_ws_sdk/gateway/connection.py`
- **问题**：`player_name` 是公开字段，文档声明为"便利指针"，宿主可能误用导致多人串上下文
- **修复**：
  1. 重命名为 `_player_name`（私有）
  2. 加只读 property：
     ```python
     @property
     def player_name(self) -> str | None:
         import warnings
         warnings.warn(
             "ConnectionState.player_name is a convenience pointer only; "
             "use PlayerMessageEvent.sender for authoritative player identity",
             DeprecationWarning, stacklevel=2,
         )
         return self._player_name
     ```
  3. 内部引用 `state.player_name` → `state._player_name`
- **验证**：现有测试通过，警告可被 pytest filter 捕获

### F-04 — Profile 从别名升级为 Protocol

- **文件**：
  - `src/mcbe_ws_sdk/profiles/__init__.py`（新增 Protocol 定义）
  - `src/mcbe_ws_sdk/profiles/legacy_mcbeai_v1/profile.py`
  - `src/mcbe_ws_sdk/config.py`
- **问题**：`AddonBridgeProfile = LegacyMcbeAiV1Profile` 是别名，无编译期契约
- **修复**：
  1. 在 `profiles/__init__.py` 定义 `AddonBridgeProfile` Protocol：
     ```python
     @runtime_checkable
     class AddonBridgeProfile(Protocol):
         bridge_request_message_id: str
         bridge_response_prefix: str
         ui_chat_prefix: str
         bridge_sender: str
         response_message_id: str
         request_version: int
         response_chunk_delay: float
         response_prelude_delay: float
     ```
  2. `LegacyMcbeAiV1Profile` 保持原样（结构上自动满足 Protocol）
  3. `AddonBridgeSettings.profile` 类型从 `AddonBridgeProfile`（旧别名）改为新的 Protocol 类型
  4. 公开 `__all__` 中加入 `AddonBridgeProfile`，顶层 `__init__.py` 同步
  5. 快照测试更新
- **验证**：`isinstance(LegacyMcbeAiV1Profile(), AddonBridgeProfile)` 为 True

### F-05 — `chunk_framed_scriptevent` 无限循环保护

- **文件**：`src/mcbe_ws_sdk/flow/flow_control.py`
- **问题**：while 循环无最大迭代限制，理论上可能死循环
- **修复**：
  ```python
  max_iterations = 10
  iteration = 0
  while True:
      iteration += 1
      if iteration > max_iterations:
          raise ProtocolError(
              f"chunk_framed_scriptevent failed to converge after "
              f"{max_iterations} iterations"
          )
      # ... existing logic
  ```
- **验证**：`tests/unit/test_flow_control.py` 现有测试通过；新增边界测试

### F-06 — `_prune_expired` O(n) 全局扫描优化

- **文件**：`src/mcbe_ws_sdk/addon/session.py`
- **问题**：每次 `_accept_chunk` 都全量扫描所有 buffer 检查 TTL
- **修复**：
  1. `_accept_chunk` 只在当前 buffer_id 已存在时检查其 TTL
  2. 全局扫描逻辑移到 `_buffer_count()` 接近 `max_buffer_ids` 时触发
  3. 保留 `_prune_expired` 方法但改为按需调用
- **验证**：`tests/unit/test_addon_bridge.py` 现有测试通过

### F-07 — 分片延迟发送逻辑去重

- **文件**：
  - `src/mcbe_ws_sdk/delivery/outbound.py`
  - `src/mcbe_ws_sdk/profiles/legacy_mcbeai_v1/delivery.py`
- **问题**：`LegacyMcbeAiV1Delivery.send_response()` 手动实现 sleep + send 循环，与 `McbeOutboundDelivery._send_chunked` 重复
- **修复**：
  1. `McbeOutboundDelivery._send_chunked` 重命名为 `send_chunked`（公开方法）
  2. `LegacyMcbeAiV1Delivery.send_response()` 调用 `self._outbound.send_chunked(payloads, "ai_resp", "legacy_mcbeai_v1_response")`
  3. 在 `FlowControlSettings.__post_init__` 中为 `chunk_delays` 增加 `ai_resp` key 支持（见 F-09）
  4. prelude_delay 保留在 `LegacyMcbeAiV1Delivery` 中（profile 特有逻辑）
- **验证**：`tests/unit/test_delivery.py` + `tests/unit/test_legacy_mcbeai_v1.py` 通过

### F-08 — 删除 `_logging.py`，统一用 structlog

- **文件**：
  - 删除 `src/mcbe_ws_sdk/_logging.py`
  - 修改所有引用该模块的文件
- **问题**：`cast(structlog.BoundLogger, ...)` 不安全；日志获取方式不统一
- **修复**：
  1. 删除 `_logging.py`
  2. 所有 `from mcbe_ws_sdk._logging import get_logger` → `import structlog` + `logger = structlog.get_logger(__name__)`
  3. 模块级 logger 改为在需要时获取（符合 structlog 惯例）
- **影响文件**：
  - `gateway/server_facade.py`
  - `gateway/connection.py`
  - `gateway/sink.py`
  - `gateway/handler.py`
  - `delivery/outbound.py`
- **验证**：全量测试通过，mypy 通过

### F-09 — `chunk_delays` key 校验

- **文件**：`src/mcbe_ws_sdk/config.py`
- **问题**：`chunk_delays` 的 key typo 静默返回 0.0
- **修复**：
  ```python
  VALID_DELAY_KINDS = frozenset({"tellraw", "scriptevent", "ai_resp"})
  
  # __post_init__ 中
  invalid = delays.keys() - VALID_DELAY_KINDS
  if invalid:
      raise ConfigurationError(
          f"flow.chunk_delays contains unknown keys: {sorted(invalid)}; "
          f"valid keys: {sorted(VALID_DELAY_KINDS)}"
      )
  ```
  3. 默认值增加 `"ai_resp": 0.15`（配合 F-07）
- **验证**：`tests/unit/test_config.py` 现有测试 + 新增 invalid key 测试

### F-10 — `_ConnectionAddonBridgeClient` 重命名为公开类

- **文件**：
  - `src/mcbe_ws_sdk/addon/service.py`
  - `src/mcbe_ws_sdk/addon/__init__.py`
  - `src/mcbe_ws_sdk/__init__.py`
  - `tests/unit/test_public_api.py`
- **问题**：私有类通过 public 方法返回 Protocol 类型，类名暗示不应被外部引用
- **修复**：
  1. 重命名 `_ConnectionAddonBridgeClient` → `ConnectionAddonBridgeClient`
  2. 添加到 `addon/__init__.py` 的 `__all__`
  3. 添加到顶层 `__init__.py` 的 `__all__`
  4. 更新快照测试
- **验证**：`tests/unit/test_public_api.py` 快照匹配

### F-12 — `py.typed` 显式声明

- **文件**：`pyproject.toml`
- **问题**：`py.typed` 依赖 hatchling 自动发现，不够显式
- **修复**：
  ```toml
  [tool.hatch.build.targets.wheel]
  packages = ["src/mcbe_ws_sdk"]
  force-include = { "src/mcbe_ws_sdk/py.typed" = "mcbe_ws_sdk/py.typed" }
  ```
- **验证**：`tests/release/test_distribution.py` 通过

### F-13 — EventBus handler 异常隔离

- **文件**：`src/mcbe_ws_sdk/gateway/events.py`
- **问题**：一个 handler 的异常会中断后续 handler 的执行
- **修复**：
  ```python
  async def emit(self, event: WsEventType, *args: Any, **kwargs: Any) -> None:
      subscribers = self._subscribers[event]
      for token_id in list(subscribers):
          # ... resolve handler ...
          try:
              result = handler(*args, **kwargs)
              if inspect.isawaitable(result):
                  await result
              elif result is not None:
                  raise TypeError(...)
          except Exception:
              logger.exception("event_handler_failed", event=event.value, handler=...)
  ```
- **验证**：`tests/unit/test_events.py` 新增异常传播测试

### F-14 — dataclass 补充 `slots=True`

- **文件**：`src/mcbe_ws_sdk/config.py`
- **问题**：`GatewaySettings`、`WebsocketTransportConfig`、`FlowControlSettings`、`AddonBridgeSettings` 未使用 slots
- **修复**：每个 `@dataclass(frozen=True)` 加 `slots=True`
- **注意**：`frozen=True` + `slots=True` + `__post_init__` 中的 `object.__setattr__` 在 Python 3.11+ 上正常工作
- **验证**：现有所有测试通过

### F-15 — 统一日志获取方式

- **文件**：与 F-08 合并处理
- **问题**：`_logging.py` + 直接 `structlog.get_logger()` 混用
- **修复**：F-08 删除 `_logging.py` 后自然统一
- **验证**：全量测试，确认无 `from mcbe_ws_sdk._logging import` 残留

### F-16 — `MessageSurfaceConfig` 注入路径

- **文件**：
  - `src/mcbe_ws_sdk/gateway/server_facade.py`
  - `src/mcbe_ws_sdk/gateway/handler.py`
- **问题**：`MessageSurfaceConfig` 无法通过 `McbeServerFacade` 构造参数传入
- **修复**：
  1. `McbeServerFacade.__init__` 加 `surface: MessageSurfaceConfig | None = None` 参数
  2. 透传给 `MinecraftProtocolHandler(self._registry, surface=surface)`
  3. `MessageSurfaceConfig` 加入顶层 public API（`__init__.py`）
  4. 快照测试更新
- **验证**：`tests/unit/test_handler.py` + `tests/unit/test_server_facade.py` 通过

### F-17 — 清理空 `capability/` 目录

- **文件**：删除 `src/mcbe_ws_sdk/capability/` 及其 `__pycache__/`
- **问题**：迁移残留，只含空的 `__pycache__/`
- **修复**：`rm -rf src/mcbe_ws_sdk/capability`
- **验证**：`find src -name capability` 无结果

---

## 跳过的条目

| # | 内容 | 原因 |
|---|------|------|
| 11 | 增加示例（echo_server, custom_commands 等） | 需要运行时 Minecraft 环境验证，超出本次代码修复范围 |

---

## 执行顺序

按依赖关系分 4 组顺序执行：

### 第 1 组：基础设施（无依赖，可先做）

| 序号 | ID | 描述 |
|------|----|------|
| 1 | F-14 | dataclass 加 `slots=True` |
| 2 | F-08 | 删除 `_logging.py`，统一用 structlog |
| 3 | F-17 | 清理空 `capability/` 目录 |

### 第 2 组：核心修复（有依赖关系）

| 序号 | ID | 描述 | 依赖 |
|------|----|------|------|
| 4 | F-01 | Frozen dataclass aliases 修复 | — |
| 5 | F-02 | `dispatch()` assert 替换 | — |
| 6 | F-03 | `player_name` 改为私有 | — |
| 7 | F-04 | Profile Protocol 定义 | — |
| 8 | F-05 | 分片迭代保护 | — |
| 9 | F-06 | TTL 扫描优化 | — |
| 10 | F-09 | `chunk_delays` key 校验 | — |

### 第 3 组：集成修复（依赖第 2 组）

| 序号 | ID | 描述 | 依赖 |
|------|----|------|------|
| 11 | F-07 | 分片延迟发送去重 | F-09（新增 `ai_resp` key） |
| 12 | F-10 | `_ConnectionAddonBridgeClient` 公开 | F-04（Profile 类型更新） |
| 13 | F-16 | `MessageSurfaceConfig` 注入路径 | — |
| 14 | F-12 | `py.typed` 显式声明 | — |

### 第 4 组：收尾

| 序号 | ID | 描述 | 依赖 |
|------|----|------|------|
| 15 | F-13 | EventBus handler 异常隔离 | — |
| 16 | F-15 | 日志统一（随 F-08 已完成） | F-08 |
| 17 | — | 更新快照测试 + `__all__` | F-04, F-10, F-16 |

---

## 验证检查清单

修复完成后逐项确认：

- [ ] `ruff check --no-cache src tests` 无新增警告
- [ ] `mypy --no-incremental src` strict 模式通过
- [ ] `pytest -p no:cacheprovider -q` 全量通过
- [ ] `python -c "import mcbe_ws_sdk; print(mcbe_ws_sdk.__version__)"` 正常
- [ ] `tests/unit/test_public_api.py` 快照匹配
- [ ] `python tools/check_dist.py dist` 通过（若生成了 dist）
- [ ] `grep -r "from mcbe_ws_sdk._logging" src/` 无结果
- [ ] `find src -name "capability" -type d` 无结果
- [ ] `grep -rn "assert isinstance" src/mcbe_ws_sdk/gateway/sink.py` 无结果（确认 F-02）
- [ ] `python -O -c "from mcbe_ws_sdk.gateway.sink import DefaultResponseSink"` 不报错

---

## 预估工作量

| 组 | 预计时间 |
|----|---------|
| 第 1 组（基础设施） | 15 min |
| 第 2 组（核心修复） | 45 min |
| 第 3 组（集成修复） | 30 min |
| 第 4 组（收尾 + 验证） | 20 min |
| **合计** | **~2 hours** |

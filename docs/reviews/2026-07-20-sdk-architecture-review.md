# MCBE-WS-SDK 架构评估报告

> 评估日期：2026-07-20  
> 评估范围：`mcbe-ws-sdk` v0.1.0 完整源码、测试、配置与示例  
> 方法论：对照业界 SDK 设计最佳实践，从 API 设计、可扩展性、类型安全、错误处理、测试、文档等维度逐项审查

---

## 一、总体评价

`mcbe-ws-sdk` 是一个架构质量**中上**的 Python SDK。核心设计决策——依赖反转、Protocol 驱动的扩展点、无全局单例、冻结配置 + 构造时校验——与主流高质量 SDK（如 `httpx`、`stripe-python`、`boto3` 的底层 transport）遵循相同原则。工程实践（strict mypy、ruff、pytest-asyncio、public API 快照测试、CI 矩阵）也达到了可独立发布的成熟度。

同常见的 SDK 相比，主要不足之处集中在：**配置/状态可变性不一致**（frozen dataclass 中可变列表被外部修改）、**抽象层次不完全**（Profile 机制有名无实）、**部分防御性代码依赖 `assert`**（在 `-O` 下被移除）以及**示例单一**。

---

## 二、架构设计评审（逐层分析）

### 2.1 分层架构 — ⭐⭐⭐⭐⭐ 优秀

```
McbeServerFacade          ← 宿主入口；拥有完整 WS 生命周期
├── ConnectionManager     ← 活跃连接 + 每连接一个 response-sender 协程
│   ├── ConnectionState   ← 传输无关的连接身份
│   └── ResponseSink      ← 宿主实现的协议，路由 OutboundText / SystemNotification
├── MinecraftProtocolHandler ← 解析 PlayerMessage、解析类型命令、渲染状态行
│   └── CommandRegistry   ← prefix/alias → type 匹配器（宿主配置）
├── EventBus              ← 类型化进程内发布/订阅，按 WsEventType 键控
├── ConnectionHook        ← 宿主实现 6 个生命周期钩子的协议
├── AddonBridgeService    ← ScriptEvent 桥：分片重组 + capability 请求/响应
│   └── AddonBridgeSession ← 每连接 pending futures + chunk buffers
├── FlowControlMiddleware  ← 字节安全的 tellraw/scriptevent 分片（461 B 硬限制）
└── McbeOutboundDelivery   ← 统一出站适配器
```

**评价**：分层清晰，每层职责单一。最值得称赞的是 **依赖反转** 贯彻了整个栈——`McbeServerFacade.__init__` 的每个 collaborator 都是：
```python
facade = McbeServerFacade(
    settings=None,    # → GatewaySettings()
    hook=None,        # → NoOpHook()
    sink=None,        # → DefaultResponseSink()
    addon=None,       # → AddonBridgeService(settings.addon)
    registry=None,    # → CommandRegistry()
)
```
这与 `httpx.Client(mounts=..., transport=...)` 或 `stripe.stripe_client.StripeClient(http_client=...)` 的设计理念完全一致。宿主控制所有行为扩展点，SDK 不反向导入宿主代码。

### 2.2 Hook / Sink 协议 — ⭐⭐⭐⭐⭐ 优秀

这是本 SDK 最出色的设计决策。`ConnectionHook` 和 `ResponseSink` 都是 `Protocol` 类，宿主只需实现需要的方法：

```python
@runtime_checkable
class ConnectionHook(Protocol):
    async def on_connected(self, state: ConnectionState) -> None: ...
    async def on_disconnected(self, state: ConnectionState) -> None: ...
    async def on_player_message(self, state: ConnectionState, player_event: PlayerMessageEvent) -> bool: ...
    async def on_ui_chat_reassembled(self, state: ConnectionState, player_name: str, message: str) -> None: ...
    async def on_command_response(self, state: ConnectionState, response: MinecraftCommandResponse) -> None: ...
    async def on_error(self, state: ConnectionState, error: MinecraftErrorFrame) -> None: ...
```

`NoOpHook` 和 `DefaultResponseSink` 定义了完整契约，宿主只需子类化覆盖所需方法——与 ASGI/WSGI 中间件模式、pytest plugin hooks 等被广泛验证的模式一致。

### 2.3 EventBus — ⭐⭐⭐⭐ 良好

类型化事件总线是 SDK 内部解耦的正确选择。值得注意的细节：
- **弱引用默认**（`weak=True`）：防止 handler 泄漏，尊重 GC
- **同步 handler 检测**：非 awaitable 的非 None 返回值会抛 `TypeError`
- **SubscriptionToken** 模式：调用方持有 token 精确取消订阅

一个小改进点是 `emit` 中 handler 抛出异常时会中断后续 handler 的执行。可以考虑 gather 模式或 try/except per handler。

### 2.4 FlowControlMiddleware — ⭐⭐⭐⭐ 良好

461 字节硬限制有实证数据支撑（222 包自动递增压力测试），设计严谨。句子优先分片 + 字节级兜底的两层保障也正确。

但 `chunk_framed_scriptevent` 的两阶段重编码循环：
```python
while True:
    refined_parts = ...
    for part in text_parts:
        refined_parts.extend(self._split_by_command_fit(part, max_len, command_line_for))
    actual_total = len(refined_parts)
    if actual_total == total_hint:
        break
    text_parts = refined_parts
    total_hint = actual_total
```
理论上存在无限循环的边界风险——如果 `encode_frame` 的输出长度对分片数量敏感（例如在 content 中嵌入 `i/n` 元数据后恰好跨越分片边界），每次重编码可能产生不同的 `total` 值。**建议**加一个最大迭代次数保护（如 5-10 次），超限后 fallback 为字符级分片。

### 2.5 Addon Bridge — ⭐⭐⭐⭐ 良好

从原 repo 移除全局单例是正确方向。`AddonBridgeSession` 的 chunk buffer 管理完善：
- TTL 过期回收
- 字节总量上限
- chunk 数量上限
- pending request 上限
- 内容变更检测

一个细微问题是 `_prune_expired` 在每次 `_accept_chunk` 时调用，且遍历所有 buffer。在大量并发连接下这是 O(n) 扫描。实际上只需要检查当前 buffer_id 的 TTL 即可，全局扫描可以改为定期后台任务。

### 2.6 Profile 机制 — ⭐⭐⭐ 可改进

```python
# profiles/__init__.py
AddonBridgeProfile = LegacyMcbeAiV1Profile  # backward-compat alias, NOT in __all__
```

这个设计有误导性。`AddonBridgeProfile` 看起来像是抽象基类，但实际上是具体类 `LegacyMcbeAiV1Profile` 的别名。`AddonBridgeSettings.profile` 的类型注解暗示可替换，但实际上没有 Protocol/ABC 定义 profile 接口合约。

**建议**：定义 `class AddonBridgeProfile(Protocol)` 声明 `bridge_request_message_id`、`bridge_response_prefix`、`bridge_sender` 等接口属性，让 `LegacyMcbeAiV1Profile` 实现该协议。这样新增 profile 时有编译期保证。

---

## 三、工程实践评审

### 3.1 类型安全 — ⭐⭐⭐⭐ 良好

- ✅ `py.typed` marker 存在
- ✅ `mypy --strict` 通过
- ✅ Pydantic v2 模型全量类型标注
- ✅ Protocol 类型用于 DI 边界
- ⚠️ `_logging.py` 使用 `cast()` 绕过 structlog 的类型不精确——建议封装 `structlog.get_logger(__name__).bind()` 调用链

### 3.2 配置验证 — ⭐⭐⭐⭐ 良好

Frozen dataclass + `__post_init__` 验证模式干净：
```python
@dataclass(frozen=True)
class FlowControlSettings:
    command_line_byte_budget: int = 461
    ...
    def __post_init__(self) -> None:
        _require_positive_int(self.command_line_byte_budget, ...)
```

但有一个正确性问题：`MinecraftCommandConfig` 是 frozen dataclass，但其 `aliases: list[str]` 字段被 `CommandRegistry.add_alias()` 直接 mutate：
```python
self._commands[command_prefix].aliases.append(alias)  # 修改了 frozen 对象的可变字段
```
这违反了 frozen 契约。如果任何代码在迭代 `aliases` 的同时调用 `add_alias`，会触发 `RuntimeError: list mutated during iteration`。**应立即修复**：改为返回新 list 或使用 tuple。

### 3.3 错误体系 — ⭐⭐⭐⭐⭐ 优秀

```python
McbeWsSdkError                     # 基类
├── ConfigurationError + ValueError  # 配置无效
├── ProtocolError + ValueError       # 协议违例
│   └── FrameTooLargeError           # 帧超限
├── BridgeError                      # bridge 错误基类
│   ├── BridgeTimeoutError           # 含 request_id
│   ├── BridgeClosedError            # 含 request_id
│   └── BridgeLimitError + ProtocolError  # 多继承，限流 + 协议双重语义
└── FacadeLifecycleError + RuntimeError   # 生命周期错误
```

层次分明，语义精确。`BridgeTimeoutError` 和 `BridgeClosedError` 携带 `request_id` 是有用的调试信息。

### 3.4 测试覆盖 — ⭐⭐⭐⭐ 良好

```
tests/unit/    — 17 个文件，覆盖每个模块
tests/smoke/   — 示例运行验证
tests/release/ — 发布标记 + 分发内容验证
```

亮点：
- `test_public_api.py` 快照测试确保 `__all__` 不被意外修改
- `test_server_facade.py` (783 行) 使用 FakeWebSocket + RecordingHook/RecordingSink 在完全不绑端口的情况下驱动完整消息路由流程
- `conftest.py` 管理 fixtures

可加强：
- 缺少 `FlowControlMiddleware` 边界条件测试（重编码无限循环、单字符超预算）
- 缺少 `AddonBridgeSession` 并发竞态测试
- 缺少 `EventBus` 异常传播测试

### 3.5 日志 — ⭐⭐⭐ 可改进

两个不一致：
1. 部分模块用内部 `_logging.get_logger()`，部分模块直接用 `structlog.get_logger()`
2. `McbeOutboundDelivery` 在模块级创建了两个 logger，而其他地方在函数/类内创建

应统一使用内部的 `get_logger()` 工厂或全部直接用 structlog。

### 3.6 代码清理 — ⭐⭐⭐ 可改进

- `src/mcbe_ws_sdk/capability/` 目录存在但只含空的 `__pycache__/`，没有 `__init__.py`。这是迁移残留，应从源码树中移除以避免混淆。

### 3.7 文档 — ⭐⭐⭐ 可改进

- ✅ docstring 详尽，含架构说明和 lifetime 示例
- ✅ README 含 Quickstart 和架构概览
- ⚠️ 只有 1 个 example（`addon-capability-call`）。一个 SDK 的理想 examples 目录应包含：
  - 简单 echo server（minimal hook）
  - 自定义命令注册
  - 自定义 sink + delivery
  - 多玩家连接示例
- ⚠️ 无 API reference 自动生成配置（如 Sphinx / mkdocs）

---

## 四、具体问题清单

### 4.1 正确性问题（应立即修复）

| # | 文件 | 问题 | 风险 |
|---|------|------|------|
| 1 | `command/registry.py:141` | `aliases.append()` 修改 frozen dataclass 的可变字段 | 并发迭代时 RuntimeError |
| 2 | `gateway/sink.py:111-114` | `assert isinstance(...)` 在 `-O` 下被移除，变为静默吞错 | 生产环境下类型不匹配无报错 |
| 3 | `gateway/connection.py:41` | `ConnectionState.player_name` 文档声明不可靠，但字段存在且可被宿主误用 | 多人场景下响应推送到错误玩家 |

### 4.2 设计改进建议

| # | 位置 | 当前状态 | 建议 |
|---|------|----------|------|
| 4 | `profiles/__init__.py` | `AddonBridgeProfile = LegacyMcbeAiV1Profile`（别名，非抽象） | 定义 `AddonBridgeProfile` Protocol，让 `LegacyMcbeAiV1Profile` 实现 |
| 5 | `flow/flow_control.py` | `chunk_framed_scriptevent` 无最大迭代保护 | 加 `max_iterations=10` 限制，超限 fallback 字符级分片 |
| 6 | `addon/session.py` | `_prune_expired` 在每次 `_accept_chunk` 时 O(n) 扫描全部 buffer | 只检查当前 buffer 的 TTL；全局扫描改为 `asyncio.create_task` 周期性任务 |
| 7 | `delivery/outbound.py` vs `profiles/.../delivery.py` | 两处分片延迟发送逻辑重复 | 让 `LegacyMcbeAiV1Delivery` 复用 `McbeOutboundDelivery._send_chunked` |
| 8 | `_logging.py` | `cast(structlog.BoundLogger, structlog.get_logger(name))` 不安全 | 调用 `structlog.get_logger(name).bind()` 或配置 structlog 返回类型 |
| 9 | `config.py` | `FlowControlSettings.chunk_delays` 的 key 无校验，typo 静默返回 0.0 | 在 `__post_init__` 中校验 key 必须属于已知集合（`{'tellraw', 'scriptevent'}`） |
| 10 | `addon/service.py` | `_ConnectionAddonBridgeClient` 是私有类但 `create_client()` 返回 Protocol | 要么公开具体类，要么确保 Protocol 描述完整契约（当前已较完整，但缺少文档） |

### 4.3 次要建议

| # | 位置 | 建议 |
|---|------|------|
| 11 | `examples/` | 增加 `echo_server.py`、`custom_commands.py`、`multi_player.py` |
| 12 | `pyproject.toml` | 显式声明 `[tool.hatch.build.targets.wheel.force-include]` 包含 `py.typed` |
| 13 | `gateway/events.py` | `emit()` 中对 handler 异常做 per-handler try/except，防止一个 handler 的异常中断其他 handler |
| 14 | `config.py` | 多个 dataclass 可加 `slots=True` 减少内存开销 |
| 15 | 全局 | 统一 structlog logger 获取方式（全部走 `_logging.py` 或全部直接 `structlog.get_logger`） |
| 16 | `gateway/handler.py` | `MinecraftProtocolHandler.__init__` 的 `MessageSurfaceConfig` 只有默认值，无法通过 `McbeServerFacade` 构造参数注入。如果宿主想自定义界面文案，只能通过 `facade.handler.surface` 属性替换：这不是不可行，但缺少一级构造器传参的便利路径 |
| 17 | `src/mcbe_ws_sdk/capability/` | 空目录只含 `__pycache__/`，无 `__init__.py` | 迁移残留，应删除 |

---

## 五、与业界 SDK 的对比

| 维度 | mcbe-ws-sdk | 业界最佳实践 | 差距 |
|------|-------------|-------------|------|
| DI / 可替换性 | Protocol + 构造注入 | 一致 | 无 |
| 配置验证 | frozen dataclass + `__post_init__` | 一致（Pydantic Settings / dataclass） | 无 |
| 公共 API 管控 | `__all__` + 快照测试 | 较少见，属于高 discipline | **优于**常见实践 |
| 类型安全 | strict mypy + py.typed + Protocol | 一致 | 无 |
| 错误体系 | 层次化 + 上下文字段 | 一致 | 无 |
| 无全局状态 | 全量构造注入 | boto3/requests 等老牌 SDK 仍有全局默认 | **优于**常见实践 |
| 扩展性（Profile） | 别名而非抽象 | 应有 Protocol/ABC | 落后 |
| 示例数量 | 1 个 | 3-5 个（不同场景） | 落后 |
| API reference | 无自动生成 | Sphinx/mkdocs + autodoc | 落后 |
| 不可变配置的完整性 | frozen dataclass 但可变字段被外部 mutate | 应真正的 immutable | 有缺陷 |

---

## 六、总结

`mcbe-ws-sdk` 的架构骨架是扎实的：依赖反转、Protocol 驱动的扩展点、无全局单例、构造时配置验证、分层清晰的模块划分——这些都与高质量 Python SDK 的最佳实践对齐。`ConnectionHook` + `ResponseSink` 的双协议注入模式尤其优雅，宿主可以在完全不触碰 SDK 内部的情况下定制全部行为。

主要改进方向有三个：
1. **修复正确性缺陷**——frozen dataclass 中可变列表被修改、`assert` 在生产代码中的使用
2. **完成抽象层次**——Profile 机制从别名升级为 Protocol/ABC
3. **提升开发者体验**——增加示例、API 文档自动生成、统一日志获取方式

在修复上述问题后，该 SDK 的架构质量可达到 **⭐⭐⭐⭐⭐ 优秀** 水平。

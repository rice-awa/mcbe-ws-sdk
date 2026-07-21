# 架构

SDK 自底向上分层。传输层之上的每一层都可注入，宿主可以覆盖任意默认实现，且 SDK
不会导入宿主代码：

```text
McbeServerFacade          ← 宿主入口；拥有完整 WS 生命周期
├── ConnectionManager     ← 活跃连接 + 每连接一条 response-sender 协程
│   ├── ConnectionState   ← 与传输无关的身份（id、send_payload、队列）
│   └── ResponseSink      ← 宿主路由 OutboundText / SystemNotification
├── MinecraftProtocolHandler  ← 解析 PlayerMessage、解析命令、渲染状态行
│   └── CommandRegistry
├── EventBus              ← 按 WsEventType 分键的进程内 pub/sub
├── ConnectionHook        ← 宿主实现的六个生命周期钩子
├── AddonBridgeService    ← ScriptEvent 桥 + 分片重组
│   └── AddonBridgeSession
├── FlowControlMiddleware ← 字节安全的 tellraw/scriptevent 分片（461 B 上限）
└── McbeOutboundDelivery  ← 统一出站适配器
```

SDK **没有**自带 `PlayerSession`。多人会话隔离是**宿主**职责：网关只透传
`PlayerMessageEvent.sender`；宿主按 `(connection_id, sender)` 分桶历史 / 锁 /
上下文。

## 依赖倒置

`McbeServerFacade.__init__` 是 keyword-only 的；每个协作者在 `None` 时折叠为网关默认值。
宿主只需子类化自己关心的部分：

- `NoOpHook` / `ConnectionHook`
- `DefaultResponseSink` / `ResponseSink`

`ConnectionHook` 共六个纯副作用钩子（全部 `-> None`）。聊天钩子签名为：

```python
async def on_player_message(
    self,
    state: ConnectionState,
    player_event: PlayerMessageEvent,
    parsed: ParsedCommand | None = None,
) -> None: ...
```

`parsed` 是 registry 的预解析匹配结果（若有）；**不是**"已消费"布尔返回值 ——
宿主自行决定如何处理自由聊天与命令。

## 每连接消息路由

1. `McbeServerFacade._on_connection` 创建连接状态，启动 `_response_sender` 协程，
   并发送 handshake + subscribe。二者成功后先 emit `WsEventType.CONNECTED`，
   再调用 `hook.on_connected`（welcome 由宿主在该 hook 中负责发送，facade 本身
   不发欢迎语）。
2. `_handle_raw` 对每帧入站数据分类：
   - **error** → `WsEventType.ERROR` + `hook.on_error`
   - **commandResponse** → `WsEventType.COMMAND_RESPONSE` + `hook.on_command_response`
   - **addon 前缀匹配** → `AddonBridgeService`（桥接 / UI 聊天重组）
   - **PlayerMessage** → `WsEventType.PLAYER_MESSAGE` +
     `hook.on_player_message(state, event, parsed=...)`
3. response-sender 排空 `state.response_queue`，经 `RouteEnvelope.from_message()` 包装后
   内联路由到 sink 的两个 `on_*` 方法（协议上不含 `dispatch`）。
4. 宿主 sink 使用 `McbeOutboundDelivery` 把排队消息变成 MC WebSocket 负载。

## 流控 — 461 B 硬上限

`FlowControlMiddleware` 强制执行 MCBE `commandLine` 字节预算（461 字节，实测得出）：

- `chunk_tellraw()` / `chunk_scriptevent()` — 带字节安全保护的语义分句
- `chunk_raw_command()` — 不做语义切分；超限抛出 `FrameTooLargeError`
- `chunk_framed_scriptevent()` — 两遍：先切分再带 `i/n` 元数据重编码

## 协议 profile

协议 profile 位于 `profiles/`，定义互操作层的线格式常量。
`McbewsV1Profile` 是唯一内置 profile（模块级单例 `MCBEWS_V1`）。线格式详见
[协议](addon-bridge-protocol.md)。

## Addon 桥运行要求

配套 Script addon 仅在世界开启 **实验 → 测试版 API**（Beta APIs）时加载。
未开启时 `scriptEventReceive` 不会触发，能力请求会超时。详见
[addon/README.zh.md — 在世界中启用](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md#%E5%9C%A8%E4%B8%96%E7%95%8C%E4%B8%AD%E5%90%AF%E7%94%A8)。

## Addon 桥信任边界

桥接层**不是**安全边界。宿主必须自行认证 / 授权谁可以调用能力；addon 仅在
世界命令路径上提供防御性 allow/denylist。详见
[addon/README.md — Trust boundary](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md#trust-boundary)
（中文见 [addon/README.zh.md — 信任边界](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md#%E4%BF%A1%E4%BB%BB%E8%BE%B9%E7%95%8C)）。

## 不导入宿主

SDK 从不导入父级宿主应用。`ConnectionState.send_payload` 是不透明的
`Callable[[str], Awaitable[None]]`——facade 把它接到 `websocket.send`。
宿主专有的装帧（`connection_id`、时间戳、LLM 消息 id）留在宿主侧。

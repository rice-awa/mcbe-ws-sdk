# 架构

SDK 是通用的 WebSocket 网关，负责传输、协议协商、把回复对应到请求、任务生命周期、分片，
以及不超出字节预算的消息投递。授权、历史记录和业务状态则由宿主负责。

```text
McbeServer                        唯一公开入口
├── ConnectionManager / ConnectionState
│   └── 连接状态机
├── ConnectionRuntime             有上限的业务队列 + 观察队列
│   └── OutboundScheduler         每连接发送容量与公平调度
├── NativeCommandClient / RequestTable
├── MinecraftProtocolHandler / CommandRegistry
├── ConnectionHook / ResponseSink  宿主行为与渲染
├── V2Bridge                       协议协商、请求、通知与流
│   └── V2Endpoint                 每连接会话（代次）与分发
├── McbeOutboundDelivery
└── FlowControlMiddleware          字节安全 tellraw/scriptevent 分片

配套附加包
router（mcbews:negotiate_v2 / mcbews:packet_v2）
└── V2Endpoint（能力注册表 + 流注册表）
```

## 入口与职责

`McbeServer` 负责连接从传输建立、协议协商、业务处理到关闭的完整过程。
业务回调运行在由 SDK 托管的队列上，因此钩子在等待回复时，读取循环仍然可以继续工作。
想在游戏中显示 `OutboundText` 或 `SystemNotification`，需要提供自己的
`ResponseSink`；默认的接收器只会把它们写入日志。

SDK 负责传输、协议协商、待回复的请求、任务生命周期、处理顺序和分片状态。
宿主负责认证、授权、历史记录和业务状态。SDK 自身没有 `PlayerSession`，
所以应用状态请按 `(connection_id, sender)` 分开保存，其中 sender 取自当前消息的
`PlayerMessageEvent.sender`。

## 连接状态机

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

- 读取循环和必要的事件订阅会先于协商启动，所以服务端在等待协商完成期间，也能读取到回复。
- 处于 `NEGOTIATING` 时，服务端只接受协商帧、必要的控制帧，以及能对应到待回复请求的原生
  `commandResponse` / `error`。业务帧会被丢弃并计数，不会排队等到以后重放。
- 只有进入 `READY_V2`，服务端才会发布业务 `CONNECTED` 事件、调用 `on_connected`，并开始路由玩家消息。
- 协商超时、协商帧格式错误、协商容量已满、确认失败或会话失效，都会让连接进入 `REJECTED`，
  接着进入 `CLOSING` / `CLOSED`。关闭码是 `1008`，关闭原因是长度有上限的简短标识。
- 被拒绝的连接不会产生成对的业务 `on_connected` / `on_disconnected`，
  但传输诊断仍会记录这次尝试。
- 迟到或已过期的代次（一次协商成功后建立的会话标识）无法让已关闭的连接重新生效。
  重新连接会开始新的协商，并产生新的代次。

附加包只接受 `mcbews:negotiate_v2` 与 `mcbews:packet_v2`。
旧的 `mcbews:bridge_req`、`session_req`、`text_resp` 事件只会被记录并拒绝，不会触发任何业务逻辑。
附加包无法关闭属于旧服务的 WebSocket，只能拒绝来自旧服务的业务帧。

## 托管调度

初始化时，SDK 会先完成传输握手和必要的订阅，再根据协商结果继续。
连接进入 `READY_V2` 后，读取循环会先对收到的帧分类，并把命令回复、协商帧和保留的载体帧各自对应起来，
然后才把业务回调加入队列。

同一玩家的聊天消息，在依次经过拦截器和钩子时保持先进先出；不同玩家之间可以并发推进。
其他原生业务事件共用一个先进先出的队列，观察者则使用另一个有长度上限的独立队列。
业务队列或观察队列超载时，SDK 会直接拒绝新的任务，而不是让队列无限增长。
某个回调抛出的异常会被隔离，不影响其他回调。

在回调中等待命令或能力的结果时，读取循环仍然可以接收回复。
连接断开时，SDK 会取消并等待托管的任务，让尚未完成的请求失败，并释放缓存。
宿主无需自己维护待回复请求表、请求任务或分片缓存。

钩子的返回值是 `None`。`on_player_message(state, event, parsed=None)` 的 `parsed`
是命令注册表的可选匹配结果，并不表示消息已经被处理掉。五个生命周期钩子分别是
`on_connected`、`on_disconnected`、`on_player_message`、`on_command_response` 与 `on_error`。

## 桥接与投递

`V2Bridge` 为每条连接管理协议协商、能力注册表和流注册表。
`V2Endpoint` 代表一个已就绪的会话（代次）：它先把回复对应到请求，再分发新的请求，
同时执行请求和流的限制，并携带元数据。
能力处理器接收 `(name, payload, CapabilityContext)`，返回一个 JSON 映射。
流使用 `stream/1`，每次产生一个逻辑项；较大的逻辑项由传输层分片，重组后只交付一次。

每条连接都有自己的出站调度器：它限制排队的帧数和字节数，为控制帧预留容量，
并让大块内容公平地分享发送机会。想避免长文本占用请求通道，
可以把 `state.send_bulk` 传给 `McbeOutboundDelivery`。

最终命令行的字节预算默认是 461 个 UTF-8 字节，只能调低。
tellraw 和带帧的 scriptevent 可以拆成多条命令；原生命令不会被拆分，超出预算时直接报错。
MCBEWS/2 的上行单独测量完整的载体帧，不沿用下行的预算。

线格式、各项限制和错误码，都以安装包内的 `profiles/mcbews_v2/manifest.json` 与
`vectors.json` 为准，详见 [MCBEWS/2 协议](protocol-v2.md)。

## 附加包要求与信任边界

配套附加包需要在目标世界开启 **实验 → 测试版 API（Beta APIs）**。
设置步骤，以及没有开启时会出现的现象，见[附加包说明](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md)。

消息经由哪个载体送达、模拟玩家 `MCBEWS_BRIDGE`，以及元数据里携带的身份，都不能当作认证。
宿主必须自己认证并授权调用方。协议自动化测试通过，也不意味着扩大了实机支持的游戏版本或平台范围。

SDK 不会导入宿主应用的模块。`ConnectionState.send_payload` 是入口接到传输层上的异步可调用对象，
SDK 不关心它的内部实现；宿主自己的消息编号、时间戳和业务封装都留在 SDK 之外。

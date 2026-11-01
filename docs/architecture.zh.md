# 架构

SDK 是通用 WebSocket 网关，负责传输、准入、请求关联、任务生命周期、分片与字节安全投递。
授权、历史与业务状态由宿主负责。

```text
McbeServer                        唯一公开入口
├── ConnectionManager / ConnectionState
│   └── 准入状态机
├── ConnectionRuntime             有界业务队列 + 观察队列
│   └── OutboundScheduler         每连接发送容量与公平调度
├── NativeCommandClient / RequestTable
├── MinecraftProtocolHandler / CommandRegistry
├── ConnectionHook / ResponseSink  宿主行为与渲染
├── V2Bridge                       准入、请求、通知与流
│   └── V2Endpoint                 每连接代次与分发
├── McbeOutboundDelivery
└── FlowControlMiddleware          字节安全 tellraw/scriptevent 分片

配套附加包
router（mcbews:negotiate_v2 / mcbews:packet_v2）
└── V2Endpoint（能力注册表 + 流注册表）
```

## 入口与职责

`McbeServer` 是唯一公开的服务入口。它始终使用受管理业务队列，并强制 MCBEWS/2 准入；
没有调度模式开关、旧回调路径或跳过准入的配置。默认响应接收器保持中性：
需要把 `OutboundText`、`SystemNotification` 渲染为游戏命令时，由宿主提供接收器。

SDK 管理传输、准入、请求等待、任务生命周期、顺序与分片状态。
宿主管理认证、授权、历史与业务状态。SDK 没有自带 `PlayerSession`；
应用状态使用本次 `PlayerMessageEvent.sender`，按 `(connection_id, sender)` 隔离。

## 准入状态机

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

- 读取循环与必要事件订阅先启动，再进行协商；入口不会在能读取回复之前等待协商。
- `NEGOTIATING` 只接受协商帧、必要控制帧，以及已关联的原生
  `commandResponse` / `error`。业务帧会被丢弃并计数，不会排队重放。
- 只有 `READY_V2` 才发布业务 `CONNECTED`、调用 `on_connected` 并路由玩家消息。
- 超时、畸形协商、容量繁忙、确认失败或会话失效会进入 `REJECTED`，再进入
  `CLOSING` / `CLOSED`。关闭码为 `1008`，原因标识长度有界。
- 被拒绝的连接没有配对的业务 `on_connected` / `on_disconnected`；
  传输诊断仍记录这次尝试。
- 迟到或过期代次不能重新打开已关闭的准入。重连会建立新的准入与新代次。

附加包只接受 `mcbews:negotiate_v2` 与 `mcbews:packet_v2`。
旧 `mcbews:bridge_req` / `session_req` / `text_resp` 事件只记录拒绝，零业务副作用。
附加包无法关闭旧服务持有的 WebSocket，只承诺拒绝其业务。

## 受管理调度

初始化先完成传输握手与必要订阅，再确定准入结果。
进入 `READY_V2` 后，读取循环先分类并关联命令回复、协商帧与保留载体，再将业务回调加入队列。

同一玩家的聊天经过拦截器与钩子时保持先进先出，不同玩家可并发推进。
其他原生业务事件共用一个先进先出队列；观察者使用独立的有界队列。
业务或观察超载会明确拒绝，不会无限增长。回调异常被隔离。

在受管理回调内等待命令或能力时，读取循环仍可接收回复。
断连会取消并等待受管理任务、使等待项失败并释放缓存。
宿主不需要维护请求等待表、请求任务或分片缓存。

钩子返回 `None`。`on_player_message(state, event, parsed=None)` 的 `parsed`
是可选注册表匹配结果，不是“已消费”标记。五个生命周期钩子分别是
`on_connected`、`on_disconnected`、`on_player_message`、`on_command_response` 与 `on_error`。

## 桥接与投递

`V2Bridge` 管理每连接的准入、能力注册表与流注册表。
`V2Endpoint` 持有已准入代次：先配对回复再分发请求，执行请求/流限制并携带元数据。
能力处理器接收 `(name, payload, CapabilityContext)`，返回 JSON 映射。
流使用 `stream/1` 逐项产生逻辑项；大项由传输层分片，重组后只交付一次。

每连接有一个出站调度器，限制排队帧数与字节数、保留控制容量并公平调度大块内容。
宿主可把 `state.send_bulk` 传给 `McbeOutboundDelivery`，让长文本不占用请求通道。

最终命令行默认预算为 461 个 UTF-8 字节，只能降低。
tellraw 与带帧 scriptevent 可以拆分；原生命令保持原子，超限拒绝。
MCBEWS/2 上行独立测量完整载体帧，不套用下行预算。

线格式、限制与错误码以安装包内的 `profiles/mcbews_v2/manifest.json` 与
`vectors.json` 为准。详见[协议合同](protocol-v2.md)。

## 附加包要求与信任边界

配套附加包需要目标世界开启 **实验 → 测试版 API（Beta APIs）**。
否则脚本不加载，准入会超时。详见[附加包说明](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md)。

载体归属、模拟玩家 `MCBEWS_BRIDGE` 与元数据身份都不是认证边界。
宿主必须认证并授权调用方。协议自动化验证不扩大实机平台或版本支持承诺。

SDK 不导入宿主应用模块。`ConnectionState.send_payload` 是由入口连接到传输层的异步可调用对象；
宿主消息编号、时间戳与业务封装留在 SDK 之外。

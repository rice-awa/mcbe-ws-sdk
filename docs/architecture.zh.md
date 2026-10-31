# 架构

本文描述尚未发布的当前源码。PyPI `0.2.1` 的范围及不变的旧默认值见[迁移指南](migration.md)。

```text
McbeServer               新入口：可靠调度与 V2Bridge
└── McbeServerFacade      传输生命周期与依赖注入
    ├── ConnectionManager / ConnectionState
    ├── ConnectionRuntime：有界业务、观察与发送调度
    ├── NativeCommandClient / RequestTable：回复关联与截止时间
    ├── MinecraftProtocolHandler / CommandRegistry
    ├── ConnectionHook / ResponseSink：宿主行为与渲染
    ├── AddonBridgeService：v1 能力与可选业务扩展
    ├── V2Bridge：协商、双向能力、元数据与流式
    └── McbeOutboundDelivery / FlowControlMiddleware：字节安全的发送
```

## 入口与职责

`McbeServer` 沿用原入口的仅限关键字参数注入与生命周期接口，选择可靠调度并提供 v2 桥接。
`McbeServerFacade()` 保持旧默认值；也可在旧入口显式注入可靠调度与 v2。
默认响应接收器保持中性，需要将 `OutboundText`、`SystemNotification` 渲染成游戏命令时，
由宿主提供接收器。

SDK 管理传输、请求等待、任务生命周期、顺序和分片状态。
宿主管理授权、历史与业务状态。SDK 没有自带 `PlayerSession`；
应用状态使用本次 `PlayerMessageEvent.sender`，按 `(connection_id, sender)` 隔离。

## 受管理调度

初始化先发送握手并订阅 `PlayerMessage`，成功后才投递连接通知。欢迎语仍由宿主发送。
读取循环先分类并关联命令回复、协商与保留附加包消息，再将业务回调加入队列。
受管理回调内等待命令或能力时，读取循环仍可接收对应回复。

同一玩家的聊天经过拦截器与钩子时保持先进先出，不同玩家可并发推进。
其他原生业务事件共用一个先进先出队列；观察者使用独立的有界队列。
业务或观察超载会明确拒绝。回调异常被隔离；断连会取消并等待受管理任务，
使等待项失败并释放缓存。这与旧读取循环逐个等待回调的顺序不同，详见[迁移指南](migration.md)。

钩子返回 `None`。`on_player_message(state, event, parsed=None)` 的 `parsed`
是可选注册表匹配结果，不是“已消费”标记。
UI 聊天使用 `on_ui_chat_reassembled(state, UiChatMessage)`；
可选 `AddonControlHook` 接收会话与审批数据对象。
旧三参数 UI 回调需要显式使用 `LegacyUiChatHookAdapter`。

## 发送与协议

响应发送器用 `RouteEnvelope.from_message()` 包装队列消息，再调用接收器的两个 `on_*` 方法。
宿主可为 `McbeOutboundDelivery` 传入 `state.send_bulk`，将长文本与请求分开调度。
有界发送器保留控制容量，并保证大批量发送得到公平调度。

最终命令行默认预算为 461 个 UTF-8 字节，只能降低。
tellraw 和带帧的 ScriptEvent 可以拆分；原生命令保持原子，超限拒绝。
v2 上行使用自身载体的限制。

`McbewsV1Profile` / `MCBEWS_V1` 定义旧协议，不是可替换运行时接口。
安装包内的清单和测试向量分别定义 v1、v2。
`V2Bridge` 按连接协商，只为已知方法回退到 v1；必需元数据和流式不支持时明确失败。
重连建立新会话，不重放请求。具体格式和资源上限见[v1](addon-bridge-protocol.md)与[v2](protocol-v2.md)。

## 附加包要求与信任边界

配套附加包需要目标世界开启 **实验 → 测试版 API（Beta APIs）**。
否则脚本不加载，能力请求会超时。详见[附加包说明](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md)。
桥接与元数据身份都不是认证边界；宿主必须认证并授权调用方。
协议自动化验证不扩大实机平台或版本支持承诺。

SDK 不导入宿主应用模块。`ConnectionState.send_payload` 是由入口连接到传输层的异步可调用对象；
宿主消息编号、时间戳与业务封装留在 SDK 之外。

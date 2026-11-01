# 将 Minecraft 连接到 Python

**mcbe-ws-sdk** 是面向 Minecraft 基岩版的 WebSocket 网关 SDK。
你可以在 Python 中处理玩家消息、执行游戏命令、调用附加包能力和接收流式数据，
不必自己处理数据分片，也不用维护待回复请求表。

SDK 与附加包之间使用 **V2 协议（MCBEWS/2）**通信。每条连接都会先完成协议协商，
成功之后你的代码才会收到业务消息。协议版本与 SDK 包版本各自独立编号。

## 先运行起来

在仓库根目录执行：

```bash
pip install -e ".[dev,docs]"
npm ci --prefix addon
npm --prefix addon run mcaddon:production
```

导入 `addon/dist/packages/mc-ws-sdk.mcaddon`，在目标世界开启
**实验 → 测试版 API（Beta APIs）**，并启用行为包。
两个示例都需要附加包来建立连接；没有开启 Beta APIs 时，附加包的脚本不会加载。

```bash
python examples/addon-server/server.py
```

在游戏中执行 `/wsserver <Python宿主IP>:8080`，再发送 `!inv` 查询背包。
接下来可以试试 `!echo hello` 或 `!stream 5`。示例只回应以 ASCII `!` 开头的消息，普通聊天不会有任何反应。

更完整的环境要求、配置和排查方法见[快速开始](getting-started.md)。

## 开发自己的宿主

- [架构](architecture.md)：连接如何建立、任务如何排队、连接如何结束。
- [扩展作者指南](extension-authors.md)：添加能力、通知、元数据和流。
- [原生事件](native-events.md)：订阅游戏事件，观察或拦截它们。
- [追踪与诊断](diagnostics.md)：跟踪一次请求，弄清失败原因。
- [V2 协议](protocol-v2.md)：消息格式、协商、分片和限制。
- [API 参考](reference.md)：类、方法和参数。
- [迁移指南](migration.md)：从 0.2.x 升级，或把 Python 包与附加包一起回退。

SDK 负责传输和协议。认证、授权与业务状态由宿主负责：
`ConnectionHook` 接收输入，`ResponseSink` 显示回复。

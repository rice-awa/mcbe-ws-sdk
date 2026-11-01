# 将 Minecraft 连接到 Python

**mcbe-ws-sdk** 是面向 Minecraft 基岩版的 WebSocket 网关 SDK。
在 Python 中处理玩家消息、执行游戏命令、调用附加包能力和接收流式数据，
无需自行维护分片或请求等待表。

**当前使用 V2 协议（MCBEWS/2）**。连接完成协议协商后开始处理业务消息。
SDK 包版本与协议版本分别管理。

## 先运行起来

在仓库根目录执行：

```bash
pip install -e ".[dev,docs]"
npm ci --prefix addon
npm --prefix addon run mcaddon:production
```

导入 `addon/dist/packages/mc-ws-sdk.mcaddon`，在目标世界开启
**实验 → 测试版 API（Beta APIs）**，并启用行为包。
两个示例都需要附加包完成连接建立；未开启 Beta APIs 时脚本不会加载。

```bash
python examples/addon-server/server.py
```

在游戏中执行 `/wsserver <Python宿主IP>:8080`，再发送 `!inv` 查询背包。
接着可试用 `!echo hello` 或 `!stream 5`。示例只处理以 ASCII `!` 开头的消息，普通聊天保持静默。

环境要求、配置和排查方法见[快速开始](getting-started.md)。

## 开发自己的宿主

- [架构](architecture.md)：连接建立、队列和生命周期。
- [扩展作者指南](extension-authors.md)：能力、通知、元数据和流。
- [原生事件](native-events.md)：订阅、观察和拦截。
- [追踪与诊断](diagnostics.md)：关联请求和定位失败。
- [V2 协议](protocol-v2.md)：消息、协商、分片和限制。
- [API 参考](reference.md)：类、方法和参数。
- [迁移指南](migration.md)：从 0.2.x 升级，以及 Python 与附加包整套回退。

SDK 负责传输和协议处理。宿主负责认证、授权与业务状态；通过 `ConnectionHook`
处理输入，通过 `ResponseSink` 显示回复。

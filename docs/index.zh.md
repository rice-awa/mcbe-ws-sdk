# mcbe-ws-sdk

面向 **Minecraft 基岩版（Bedrock）** 的通用 **WebSocket 网关 SDK**。

本包拥有 **WS 传输**、**数据包协议**与**字节安全的命令分片**（461 字节硬上限）。
宿主通过两个协议注入行为——`ConnectionHook` 与 `ResponseSink`——并由
`McbeServerFacade` 驱动整条链路。

SDK 内部**不包含**消息 broker 或 LLM worker —— 这些关切完全属于宿主应用。

```text
Minecraft 客户端  ←── /wsserver IP:端口 ──→  你的 Python 宿主（本 SDK）
```

## 你能用它做什么

| 你想做… | 用不用这个 SDK |
|---------|----------------|
| 监听玩家聊天并自动回复 | ✅ |
| 从 Python 执行 `time set day` 这类命令 | ✅ |
| 通过 addon 读玩家背包 / 坐标 | ✅ |
| 做一个带 AI 对话的机器人（LLM 部分自己写） | ✅ |
| 直接当现成的 AI 服务器用 | ❌ 由宿主应用负责 |
| 写 Java 版 Minecraft 插件 | ❌ 只面向基岩版 WebSocket |

## 单向能力模型

SDK 从 Python 宿主向 Minecraft addon 发送桥接请求并接收响应。**不包含入站能力注册表分发**——
所有能力处理逻辑由 addon 端拥有。

唯一内置协议 profile 是 `McbewsV1Profile`（`MCBEWS_V1`）。

## 双层接口

| 层级 | 用法 |
|------|------|
| 高层 | 实现 `ConnectionHook` + `ResponseSink`，运行 `McbeServerFacade` |
| 低层 | 订阅按 `WsEventType` 分键的 `EventBus` |

## 下一步

- **[快速开始](getting-started.md)** — 安装、5 分钟上手、最小回声机器人、FAQ
- [架构](architecture.md) — 分层栈与依赖倒置
- [协议](addon-bridge-protocol.md) — mcbews v1 桥接线格式
- [API 参考](reference.md) — 自动生成正文见英文站；本站含导读

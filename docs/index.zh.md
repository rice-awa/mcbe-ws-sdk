# mcbe-ws-sdk

面向 Minecraft 基岩版的 WebSocket 通信 SDK。新宿主从 `McbeServer` 开始，
直接等待命令与能力调用；SDK 管理关联请求、任务和分片。
模型服务、消息代理、认证与业务历史由宿主负责。

> **版本范围：** 当前 PyPI 发布版为 `0.2.1`。本站的新入口、v2、元数据和持续流描述
> 尚未发布的当前源码，需要可编辑安装。安装 PyPI `0.2.1` 不包含这些新增接口。

## 选择入口

| 需求 | 入口 |
| --- | --- |
| 新宿主、可在回调中等待回复 | `McbeServer`，默认可靠调度并探测 v2 |
| 保持现有串行回调顺序 | `McbeServerFacade`，默认值不变 |
| 自定义能力与元数据 | 双向能力注册表及协商后的 v2 请求 |
| 持续数据 | 协商 `stream/1` 后使用异步流 |
| 原生事件 | `state.native_events` 订阅与拦截 |

旧附加包只回退到已知 v1 方法；元数据与流式不丢字段降级。
附加包世界必须开启 **实验 → 测试版 API（Beta APIs）**。
当前自动化证据不代表实机、完整版本矩阵或新平台支持已验收。

## 开始使用

- [快速开始](getting-started.md)：安装源码、连接游戏、命令与背包查询。
- [迁移指南](migration.md)：旧接口、回调顺序、默认值与失败行为。
- [扩展作者](extension-authors.md)：自定义能力、元数据、事件订阅与流式。
- [架构](architecture.md)、[v1 协议](addon-bridge-protocol.md)、[v2 合同](protocol-v2.md)。
- [API 参考](reference.md)：英文源码生成的参考正文。

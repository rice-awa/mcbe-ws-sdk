# API 参考

完整的 API 参考只有英文版，包含签名、参数和源码文档；本站的使用指南则提供中文版本。

`McbeServer` 是唯一公开的服务入口，`server.v2` 提供 `V2Bridge`，
用来调用能力、发送通知和处理流。入口与用法见[快速开始](getting-started.md)和
[扩展作者指南](extension-authors.md)，线协议见 [MCBEWS/2 协议](protocol-v2.md)。

## 如何查看完整 API

1. 使用页面顶部的**语言切换器**切换到 **English**。
2. 打开导航中的 **API Reference**。

完整参考覆盖：

- 公共包 `mcbe_ws_sdk` 的再导出
- `gateway`：连接、协议协商、钩子、运行时、原生事件与发送调度
- `profiles.mcbews_v2`：编解码、传输、协商运行时、端点与流
- `flow` / `protocol` / `delivery` / `command` / `capability`
- `config` / `logging` / `tracing` / `diagnostics` / `errors`

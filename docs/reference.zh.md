# API 参考

完整 API 参考提供英文版，包含签名、参数与源码文档。本站的使用指南另有中文译本。

`McbeServer` 是唯一公开的服务入口；`server.v2` 提供 `V2Bridge`，
用于能力、通知与流。入口与用法见[快速开始](getting-started.md)与
[扩展作者指南](extension-authors.md)，线协议见[协议合同](protocol-v2.md)。

## 如何查看完整 API

1. 使用页面顶部的**语言切换器**切换到 **English**。
2. 打开导航中的 **API Reference**。

完整参考覆盖：

- 公共包 `mcbe_ws_sdk` 的再导出
- `gateway`：连接、协议协商、钩子、运行时、原生事件与发送调度
- `profiles.mcbews_v2`：编解码、传输、协商运行时、端点与流
- `flow` / `protocol` / `delivery` / `command` / `capability`
- `config` / `logging` / `tracing` / `diagnostics` / `errors`

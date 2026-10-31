# API 参考

API 签名与文档字符串 以源码为准（**公开 API 文档字符串为英文**），因此自动生成的 API 正文只维护**英文版**一份，避免双语重复渲染导致交叉引用冲突。面向用户的指南页（本站 `/zh/`）另有中文译本。

新入口 `McbeServer` 与 v2、流式导出属于尚未发布的源码，不在 PyPI `0.2.1` 中。
入口差异见[迁移指南](migration.md)，注册与使用见[扩展作者指南](extension-authors.md)。

## 如何查看完整 API

1. 使用页面顶部的 **语言切换器** 切换到 **English**
2. 打开导航中的 **API Reference**

完整参考覆盖：

- 公共包 `mcbe_ws_sdk` 再导出
- `gateway` / `addon` / `capability` / `flow` / `protocol` / `profiles` / `delivery` / `command`
- `config` / `logging` / `errors`
- v2 协商运行时、端点与流注册、消费接口

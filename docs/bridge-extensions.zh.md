# 桥接扩展

本文描述尚未发布的当前源码；PyPI `0.2.1` 不包含本轮新增接口。安装与兼容说明见[迁移指南](migration.md)。

通用分片与可选 MCBEWS/1 业务适配保留在同一个包中。
`ReassemblyCore` 只处理顺序、重复、UTF-8 大小和过期。
同一连接的桥接与 UI 通道共享缓冲数量、总字节配额；相同重复分片不续期。
适配层释放完成缓冲，并将能力响应的过期转换为对应请求失败。

`mcbe_ws_sdk.extensions.mcbews_v1` 承载 UI 聊天、会话、审批的数据模型、
编解码、控制分类和文字／会话发送。原根级与 `profiles.mcbews_v1` 导入继续可用。
线格式、仅末帧携带用量及原子 `SESSION_RESPONSE_TOO_LARGE` 回复不变。

关闭可选业务处理：

```python
from mcbe_ws_sdk import AddonBridgeService, AddonBridgeSettings, McbeServer

addon = AddonBridgeService(AddonBridgeSettings(), extensions=False)
server = McbeServer(addon=addon)
```

连接生命周期、原生命令、自定义能力仍可运行。
保留的 UI／会话／审批帧会被消费，但不解码或调用业务回调。
默认 `extensions=True` 保持兼容；禁用后显式调用会话的
`handle_ui_chat_chunk` 会抛出 `ProtocolError`。

附加包的 `bridge/reassembly.ts` 保存不透明分片；可选
`extensions/textResponse.ts` 校验角色、对话与用量。
`bridge/responseSync.ts` 保留兼容转出。原生命令与能力路由不要求注册文字响应处理器。

边界测试见 `tests/unit/test_reassembly_extensions.py` 与
`addon/tests/reassembly.test.ts`；原 v1 样本仍为合同依据。

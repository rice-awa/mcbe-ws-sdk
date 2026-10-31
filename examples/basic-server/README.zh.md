# 基础 WebSocket 服务

本示例使用尚未发布的当前源码中的 `McbeServer`；PyPI `0.2.1` 不包含这个新入口。
SDK 管理可靠调度、命令请求等待与分片。示例打印聊天，用 tellraw 回复，并过滤外部消息回声。

## 运行

在仓库根目录执行：

```bash
pip install -e ".[dev]"
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

游戏里执行 `/wsserver <Python 宿主 IP>:8080`，然后发送聊天。
同机可用 `127.0.0.1`，跨设备使用局域网 IP，并在防火墙放行端口。
`!wscmd time query daytime` 演示直接等待原生命令响应，不需要附加包。
通过 `--log-level DEBUG` 查看诊断，按 `Ctrl+C` 停止。

同一玩家的回调按顺序执行，不同玩家可并发推进；钩子内等待命令时读取循环继续接收回复。
命令返回 `MinecraftCommandResponse`，需要检查 `statusCode`。
SDK 管理超时、断连和取消，宿主不维护等待表或后台请求任务。
取消不能撤销已经执行的命令。旧入口顺序不变，详见[迁移指南](../../docs/migration.zh.md)。

本示例不需要附加包。需要查询背包、自定义能力、元数据、事件与持续流时，
参考[附加包示例](../addon-server/README.zh.md)与[扩展作者指南](../../docs/extension-authors.zh.md)。
启用附加包的世界必须打开 **实验 → 测试版 API（Beta APIs）**。
示例没有鉴权，应在可信本机或局域网运行；实机与完整兼容矩阵仍待验收，不扩大平台或版本支持承诺。

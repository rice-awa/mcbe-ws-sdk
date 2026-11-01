# 基础 WebSocket 服务

这是一个最小的 `McbeServer` 宿主，演示了连接生命周期、SDK 托管的钩子、`!` 命令分发、
tellraw 回复，以及原生 `commandRequest` 的关联。协议协商、请求等待、超时和分片都由 SDK 负责，
示例只实现聊天行为。

## 运行

在仓库根目录执行：

```bash
pip install -e ".[dev]"
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

在游戏里执行 `/wsserver <Python 宿主 IP>:8080`。Python 与游戏在同一台机器上时可以用 `127.0.0.1`；
从其他设备连接时，请使用运行 Python 的机器的局域网 IP，并在它的防火墙中放行该端口。
添加 `--log-level DEBUG` 可以查看连接生命周期的诊断信息，按 `Ctrl+C` 停止服务。

## 输入规则

示例只处理去掉首尾空白后以 ASCII `!` 开头的消息。
空消息或纯空白、普通聊天、单独的 `help` / `帮助`、出现在句子中间的 `!`，以及全角的 `！`，
都会被忽略：示例不会回复，也不会执行命令或调用能力。单独一个 `!` 会得到一次用法提示，
未知的 `!command` 也会得到一次提示。这条规则只作用于示例本身，
不会过滤 SDK 的协议帧和原生事件。

## 命令

| 命令 | 行为 |
| --- | --- |
| `!help` / `!帮助` | 显示帮助 |
| `!wscmd <command>` | 等待原生命令响应，例如 `!wscmd time query daytime` |

请在世界中安装配套附加包，并开启 **实验 → 测试版 API（Beta APIs）**。
虽然这个示例没有调用任何附加包能力，但协议协商仍然要靠附加包来完成；命令的执行则走原生 WebSocket 接口。
`!wscmd` 使用 SDK 的原生命令客户端，返回带类型的 `MinecraftCommandResponse`，请检查其中的 `statusCode`。
没有安装附加包时，服务端会记录 `NEGOTIATION_TIMEOUT` 并关闭连接，也不会退而去处理聊天消息。

## 行为

- `on_connected` 只有在 MCBEWS/2 协议协商进入 `READY_V2` 之后才会触发。
- 同一玩家的回调按顺序执行，不同玩家可以并发推进；钩子在等待命令结果时，读取循环仍会继续接收回复。
- 示例用 `is_external_echo()` 过滤外部的 tellraw 回声，避免自己回复自己。
- 取消只会结束等待，无法撤销已经执行的命令。

需要查询背包、能力、元数据和流时，请参考[附加包示例](../addon-server/README.zh.md)
和[扩展作者指南](../../docs/extension-authors.zh.md)。
使用附加包的世界必须开启 **实验 → 测试版 API（Beta APIs）**。

示例没有鉴权，请在可信的本机或局域网环境中运行；用于生产环境前，需要先加上宿主侧的授权。
运行这些自动化示例，并不意味着承诺支持新的 Minecraft 平台或版本。

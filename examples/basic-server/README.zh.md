# 基础 WebSocket 服务

最小的 `McbeServer` 宿主。展示连接生命周期、受管理钩子、`!` 命令分发、
tellraw 回复与原生 `commandRequest` 关联。SDK 负责协议协商、请求等待、超时与分片；
示例负责聊天行为。

## 运行

在仓库根目录执行：

```bash
pip install -e ".[dev]"
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

游戏里执行 `/wsserver <Python 宿主 IP>:8080`。同机可用 `127.0.0.1`，
跨设备使用局域网 IP，并在防火墙放行端口。
通过 `--log-level DEBUG` 查看生命周期诊断，按 `Ctrl+C` 停止。

## 输入规则

只有去掉首尾空白后以 ASCII `!` 开头的消息会被处理。
空串或空白、普通聊天、裸 `help` / `帮助`、句中 `!` 与全角 `！` 都会静默：
不回复、不执行命令、不调用能力。裸 `!` 返回一次用法提示；未知 `!command`
返回一次提示。该规则只作用于示例，SDK 协议与原生事件不受影响。

## 命令

| 命令 | 行为 |
| --- | --- |
| `!help` / `!帮助` | 显示帮助 |
| `!wscmd <command>` | 等待原生命令响应，例如 `!wscmd time query daytime` |

在世界中安装配套附加包，并开启 **实验 → 测试版 API（Beta APIs）**。
本示例通过附加包完成协议协商，命令执行使用原生 WebSocket 接口。`!wscmd` 使用 SDK 的原生命令客户端，返回类型化的
`MinecraftCommandResponse`；请检查 `statusCode`。未安装附加包时服务端记录
`NEGOTIATION_TIMEOUT` 并关闭连接，绝不回退处理聊天。

## 行为

- `on_connected` 只在 MCBEWS/2 协议协商进入 `READY_V2` 后触发。
- 同一玩家的回调保持顺序，不同玩家可并发推进；钩子等待命令时读取循环继续接收回复。
- 示例用 `is_external_echo()` 过滤外部 tellraw 回声，避免自己回复自己。
- 取消会结束等待，但不能撤销已经执行的命令。

需要查询背包、能力、元数据与流时，参考[附加包示例](../addon-server/README.zh.md)
与[扩展作者指南](../../docs/extension-authors.zh.md)。
启用附加包的世界必须打开 **实验 → 测试版 API（Beta APIs）**。

示例没有鉴权，应在可信本机或局域网运行；生产环境需要宿主授权。
自动化示例不扩大 Minecraft 平台或版本支持承诺。

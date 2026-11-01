# 附加包能力演示服务器

这是一个通过 `V2Bridge` 调用配套附加包的 `McbeServer` 宿主，演示了能力请求、元数据、通知和流；
协议协商、回调、请求等待、超时和分片都由 SDK 负责。

## 构建附加包

默认包包含本服务器调用的全部能力：

```bash
cd addon
npm ci
npm run build
npm run mcaddon
```

`--base` 精简包只包含两个只读快照能力，无法支持 `!echo`、`!stream`、`!stream-cancel`
和 `!cmd`：

```bash
npm run build:base
npm run mcaddon:base
```

导入生成的 `.mcaddon`。在目标世界的设置中开启 **实验 → 测试版 API（Beta APIs）**，
并启用行为包。没有开启 Beta APIs 时，脚本不会加载，协议协商也会超时。
完整步骤见[附加包设置](../../addon/README.zh.md#在世界中启用)。

默认构建会打包 `addon/scripts/main.demo.ts` 并调用 `registerDemoCapabilities()`，
因此不需要手动往附加包里复制文件。`demo.echo`、`demo.ticks` 流和
`run_world_command` 都在 `demoCapabilities.ts` 中统一注册，其中世界命令仍然受命令禁止列表约束。

## 运行

在仓库根目录执行：

```bash
python examples/addon-server/server.py
python examples/addon-server/server.py --host 0.0.0.0 --port 8080 --log-level DEBUG
```

在游戏里执行 `/wsserver <Python 宿主 IP>:8080`。从其他设备连接时，请使用运行 Python 的机器的局域网 IP，
并在它的防火墙中放行监听端口。按 `Ctrl+C` 可以停止 Python。
只有确实需要在日志里查看完整数据包时，才添加 `--log-raw`。

## 输入规则

示例只处理去掉首尾空白后以 ASCII `!` 开头的消息。
空消息或纯空白、普通聊天、单独的 `help` / `帮助`、出现在句子中间的 `!`，以及全角的 `！`，
都会被忽略：示例不会回复，也不会执行命令，更不会调用能力或流。单独一个 `!` 会得到一次用法提示，
未知的 `!command` 也会得到一次提示。带 `!` 前缀的中文别名依然可用。
这条规则不影响 SDK 的协议帧和原生事件。

## 命令

| 聊天命令 | 行为 | 包 |
| --- | --- | --- |
| `!player [target]` / `!玩家` | `get_player_snapshot`，默认当前玩家 | 默认 + 精简 |
| `!inv [target]` / `!背包` | `get_inventory_snapshot` | 默认 + 精简 |
| `!wscmd <command>` / `!ws命令` | 原生 `command_client.run`，不需要附加包能力 | 默认 + 精简 |
| `!cmd <command>` / `!命令` | `run_world_command`，以发言玩家为上下文，命中禁止列表的命令被拒绝 | 仅默认 |
| `!echo <text>` | `demo.echo`，附带 `example.label` 与 `example.player` 元数据 | 仅默认 |
| `!stream [count]` | 消费有限的 `demo.ticks` 直到正常结束（默认 5 项） | 仅默认 |
| `!stream-cancel [count]` | 读取无限生产者的前若干项后取消（默认 3 项） | 仅默认 |
| `!help` / `!帮助` | 查看帮助 | 默认 + 精简 |

使用 `--base` 精简包时，演示命令会返回一条可读的 `UNSUPPORTED_METHOD` 错误，并提示改用默认构建。
`run_world_command` 始终应用命令禁止列表；需要更严格时，可以向
`handleRunWorldCommand` 传入明确的允许列表。

### 选择器与执行上下文

用 `!cmd testfor @s` 选中自己，或用 `!cmd testfor @e[type=minecraft:cow]`
查找你所在维度里的牛。`!cmd` 会传入你的完整玩家名，因此 `@s`、最近玩家选择和相对坐标
都以你的执行上下文为准；如果命令执行前你已经离线，请求会返回错误。
`!wscmd` 则使用 WebSocket 连接自带的原生上下文。

直接调用 `run_world_command` 时，可以省略 `player`，此时沿用主世界维度的上下文，
详见[世界命令安全](../../addon/README.zh.md#世界命令安全)。`successCount` 为零表示命令成功生效的次数为零，
示例会明确显示这一点。

按实体类型选择时，需要写成 `@e[type=minecraft:cow]`，单写 `cow` 不会按类型筛选。
`setblock` 的位置参数需要三个坐标，例如 `setblock ~ ~ ~ stone`；它仍然被附加包的禁止列表拦截。

## 顺序、失败与取消

`McbeServer` 要等协议协商完成后才开始处理业务。同一玩家的回调按先进先出的顺序执行，
其他玩家可以各自独立推进。钩子在等待结果时，读取循环仍会继续接收回复，
所以示例不需要维护请求等待表、分片状态或后台请求任务。

协商后的请求会返回响应信封，成功的数据放在 `result` 中；远端返回协议错误时，
会抛出带有 `code` 的 `RemoteCallError`；原生命令的响应则带有 `statusCode`。
超时、不支持的能力和传输故障都会让调用明确失败。调用失败时，玩家会看到能力名、
错误码、一段简短的说明（最多 256 个 UTF-8 字节）和行动提示，内部请求编号与堆栈只写入日志。

请用 `async with stream` 消费流，提前退出时就会取消生产者。取消并不会撤销已经执行的世界修改。
慢消费者的缓存写满后会被终止，目前还没有接收额度机制。

请使用 `event.sender`，不要使用连接上已弃用的“最近发言玩家”指针。示例还会过滤掉外部的 tellraw 回声。

示例没有鉴权，只适合在可信的本机或局域网环境中运行；用于生产环境前，需要先加上宿主侧的授权。
运行这些自动化示例，并不意味着承诺支持新的 Minecraft 平台或版本。
注册、事件订阅和协议限制见[扩展作者指南](../../docs/extension-authors.zh.md)。

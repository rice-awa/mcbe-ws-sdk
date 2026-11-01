# 附加包能力演示服务器

通过 `V2Bridge` 调用配套附加包的 `McbeServer` 宿主。展示能力请求、元数据、通知与流；
准入、回调、请求等待、超时与分片由 SDK 负责。

## 构建附加包

基础包，包含两个只读快照：

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

完整演示包，`!echo`、`!stream`、`!stream-cancel` 与 `!cmd` 需要它：

```bash
npm run build:demo
npm run mcaddon:demo
```

导入生成的 `.mcaddon`。在目标世界设置中开启 **实验 → 测试版 API（Beta APIs）**，
并启用行为包。未开启时脚本不会加载，准入会超时。
详见[附加包设置](../../addon/README.zh.md)。

演示构建会打包 `addon/scripts/main.demo.ts` 并调用 `registerDemoCapabilities()`，
不需要手动复制文件。`demoCapabilities.ts` 是 `demo.echo`、`demo.ticks` 流与受限
`run_world_command` 的共同注册源。

## 运行

在仓库根目录执行：

```bash
python examples/addon-server/server.py
python examples/addon-server/server.py --host 0.0.0.0 --port 8080 --log-level DEBUG
```

游戏里执行 `/wsserver <Python 宿主 IP>:8080`。跨设备使用局域网 IP，
在防火墙放行监听端口；按 `Ctrl+C` 停止 Python。
只有确实需要完整数据包时才添加 `--log-raw`。

## 输入规则

只有去掉首尾空白后以 ASCII `!` 开头的消息会被处理。
空串或空白、普通聊天、裸 `help` / `帮助`、句中 `!` 与全角 `！` 都会静默：
不回复、不执行命令、不调用能力或流。裸 `!` 返回一次用法提示；未知 `!command`
返回一次提示。带前缀的中文别名保留。SDK 协议帧与原生事件不受该规则影响。

## 命令

| 聊天命令 | 行为 | 包 |
| --- | --- | --- |
| `!player [target]` / `!玩家` | `get_player_snapshot`，默认当前玩家 | 基础 |
| `!inv [target]` / `!背包` | `get_inventory_snapshot` | 基础 |
| `!wscmd <command>` / `!ws命令` | 原生 `command_client.run`，不需要附加包能力 | 基础 |
| `!cmd <command>` / `!命令` | `run_world_command`，受限 | 演示 |
| `!echo <text>` | `demo.echo`，附带 `example.label` 与 `example.player` 元数据 | 演示 |
| `!stream [count]` | 消费有限的 `demo.ticks` 直到正常结束（默认 5 项） | 演示 |
| `!stream-cancel [count]` | 读取无限生产者的前若干项后取消（默认 3 项） | 演示 |
| `!help` / `!帮助` | 查看帮助 | 基础 |

使用基础包时，演示命令会返回可读的 `UNSUPPORTED_METHOD` 错误，并提示使用演示构建。
即使在演示构建中，`run_world_command` 仍受限制：默认允许列表只有 `say`，
危险命令禁止列表仍然生效。

## 顺序、失败与取消

`McbeServer` 强制 MCBEWS/2 准入。同一玩家的回调保持先进先出，其他玩家可独立推进。
钩子等待业务时，读取循环仍接收回复。示例不维护请求等待表、分片状态或后台请求任务。

协商请求返回响应信封，成功数据在 `result` 中；远端协议错误抛出带 `code` 的
`RemoteCallError`。原生响应包含 `statusCode`。超时、不支持的能力和传输故障都会明确结束调用。
玩家看到的失败包含能力名、错误码、最多 256 个 UTF-8 字节的可读说明和行动提示；
内部请求编号与堆栈只留在日志中。

流通过 `async with stream` 消费，提前退出会取消生产者。
取消不撤销已经执行的世界修改。慢消费者会在有界缓存下终止，当前没有接收额度机制。

使用 `event.sender`，不要使用连接上已弃用的最近玩家指针。示例会过滤外部 tellraw 回声。

示例没有鉴权，适合可信的本机或局域网环境；生产环境需要宿主授权。
自动化示例不扩大 Minecraft 平台或版本支持承诺。
注册、事件订阅和协议限制见[扩展作者指南](../../docs/extension-authors.zh.md)。

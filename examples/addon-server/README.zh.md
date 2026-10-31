# 附加包能力演示服务器

本示例使用**尚未发布的当前源码**中的 `McbeServer`，不是 PyPI `0.2.1`。
SDK 管理回调、请求等待、超时与分片；钩子直接等待命令或能力调用。

## 运行

从仓库根目录执行：

```bash
pip install -e ".[dev]"
cd addon
npm install
npm run build
npm run mcaddon
```

导入生成的 `.mcaddon`。在目标世界设置中开启
**实验 → 测试版 API（Beta APIs）**，并启用行为包。
未开启时脚本不会加载，能力请求会超时。详见[附加包设置](../../addon/README.zh.md)。

回到仓库根目录启动 Python：

```bash
python examples/addon-server/server.py
python examples/addon-server/server.py --host 0.0.0.0 --port 8080 --log-level DEBUG
```

游戏里执行 `/wsserver <Python 宿主 IP>:8080`。跨设备使用局域网 IP，
在防火墙放行监听端口；按 `Ctrl+C` 停止 Python。

| 聊天命令 | 行为 |
| --- | --- |
| `!player [target]` | 调用 `get_player_snapshot`，默认查询当前玩家 |
| `!inv [target]` | 调用 `get_inventory_snapshot` |
| `!wscmd <command>` | 使用原生 `state.command_client.run`，不依赖附加包 |
| `!cmd <command>` | 调用附加包 `run_world_command`，需要另外启用处理器 |
| `!echo <text>` | 调用自定义 `demo.echo`，附带自定义元数据 |
| `!stream [count]` | 消费有限的 `demo.ticks`，直到正常结束，默认 5 项 |
| `!stream-cancel [count]` | 从无限生产者读取指定项数后取消，默认读取 3 项 |
| `!help` | 查看命令帮助 |

## 安装可选的演示处理器

背包与玩家快照使用内置能力。自定义回声和流需要把示例注册模块接入附加包：

1. 将 `examples/addon-server/capabilities.ts` 复制到 `addon/scripts/demoCapabilities.ts`。
2. 在 `addon/scripts/main.ts` 初始化时导入并调用一次：

   ```typescript
   import { registerDemoCapabilities } from "./demoCapabilities";
   registerDemoCapabilities();
   ```

3. 在 `addon/` 中重新执行 `npm run build`、`npm run mcaddon`，重新导入生成的包，
   在开启 **实验 → 测试版 API（Beta APIs）** 的目标世界启用它，重新连接并协商新会话。

这会注册 `demo.echo` 和 `demo.ticks`，无需修改 SDK 协议或分片逻辑。
不要重复注册，同名会被拒绝。`!echo` 发送 `example.label`、`example.player` 元数据；
这些值只是应用上下文，不证明玩家身份，也不授予权限。

`run_world_command` 默认关闭。如有需要，通过附加包路由器显式注册现有处理器。
默认允许列表只有 `say`，禁止列表仍生效。详见[信任边界](../../addon/README.zh.md)。

## 顺序、失败与取消

`McbeServer` 启用可靠调度，并创建负责协商的 `V2Bridge`。
同一玩家的回调保持先进先出，其他玩家可独立推进。
钩子等待业务时，读取循环仍接收回复。示例不维护请求等待表、分片状态或后台请求任务。

协商请求返回响应信封，成功数据在 `result` 中；v2 远端错误抛出 `RemoteCallError`。
v1 回退在 `result` 中保留能力级 `ok: false`。原生响应包含 `statusCode`。
超时、不支持的能力和传输故障都会明确结束调用。

自定义元数据和流式要求 v2 能力；旧附加包只支持已知 v1 方法，不能静默丢弃元数据，
也不会把完整响应包装成假流。流通过 `async with stream` 消费，提前退出会取消生产者。
取消不撤销已经执行的世界修改。慢消费者会在有界缓存下终止，当前没有接收额度机制。

旧 `McbeServerFacade` 的串行默认值保持不变；其内联钩子不能等待由同一读取循环接收的回复，
详见[迁移表](../../docs/migration.zh.md)。新宿主无需为这一旧限制自行创建任务。

使用 `event.sender`，不要使用连接上已弃用的最近玩家指针。示例会过滤外部 tellraw 回声。
示例没有鉴权，适合可信的本机或局域网环境；生产环境需要宿主授权。
自动化示例不扩大 Minecraft 平台或版本支持承诺，实机验收仍待完成。
注册、事件订阅和协议限制见[扩展作者指南](../../docs/extension-authors.zh.md)。

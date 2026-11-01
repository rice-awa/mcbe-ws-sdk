[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)

# MCBE WebSocket 桥接附加包

`mcbe-ws-sdk` 的 TypeScript 配套实现，负责 MCBEWS/2 协议在 Minecraft 一侧的工作：
协议协商、数据包载体、能力注册表和请求流。协议的另一侧由 Python 宿主实现。

当前附加包使用 **V2 协议（MCBEWS/2）**，接收两个 scriptevent 标识：

- `mcbews:negotiate_v2`：用于协议协商的控制消息；
- `mcbews:packet_v2`：承载逻辑消息的载体。

已退休的 `mcbews:bridge_req`、`mcbews:session_req` 和 `mcbews:text_resp`
只会被记录并拒绝：附加包不会解析、排队、回复或分发它们。
旧服务持有的 WebSocket 无法由附加包关闭，附加包只能拒绝该服务发来的业务帧。

SDK、npm 项目、pack、协议和 Script API 各自独立编号，
当前版本见[版本说明](../docs/migration.zh.md#版本号分别表示什么)。

## 环境要求

- 支持 Script API 的 Minecraft 基岩版，并且目标世界已开启 **实验 → 测试版 API（Beta APIs）**。
- 构建需要 Node.js 22。
- `package.json` 中固定的依赖：
  - `@minecraft/server` 2.8.0
  - `@minecraft/server-ui` 2.1.0
  - `@minecraft/server-gametest` `1.0.0-beta.1.26.30-stable`
- 两个包清单的 `min_engine_version` 都是 `[1, 26, 30]`。

自动化构建和测试通过，并不代表已经在真实游戏中验证过。部署前请自行核对游戏版本与平台。

## 构建

```bash
npm ci
npm run build       # tsc 类型检查 + esbuild 打包
npm run mcaddon     # 组装 .mcaddon
```

默认包包含完整的能力集：两个只读快照、`demo.echo`、`demo.ticks` 流和
`run_world_command`。导入 `dist/packages/mc-ws-sdk.mcaddon` 即可使用。

精简包只包含两个只读快照：

```bash
npm run build:base
npm run mcaddon:base
```

传入 `--base` 或设置 `MCBE_BASE=1` 时，构建以 `scripts/main.ts` 为入口；默认构建则使用
`scripts/main.demo.ts`。两个变体最终都输出到同一个文件 `dist/scripts/main.js`，
精简包的产物是 `mc-ws-sdk-base.mcaddon`。`lib/` 只是类型检查产生的中间文件，游戏不会加载它。

打包时，`@minecraft/server`、`@minecraft/server-ui` 和
`@minecraft/server-gametest` 作为外部模块保留，它们的版本由包清单指定。

## 在世界中启用

1. 将生成的 `.mcaddon` 导入 Minecraft，或手动复制行为包 / 资源包。
2. 打开**目标世界**设置（新建世界，或对已有世界点**编辑**）。
3. 在**实验**（Experiments）中开启 **测试版 API（Beta APIs）**。
4. 启用行为包（如有提示，也启用配套资源包）。
5. 进入世界后执行 `/wsserver <主机IP>:8080`。

没有开启 Beta APIs 时，脚本不会加载：协议协商会超时，游戏里也看不到任何桥接活动。
开启实验性玩法可能导致该世界无法解锁部分成就，建议使用专用的测试世界。

## 工作方式

```text
scriptEventReceive
├── mcbews:negotiate_v2  → 挑战 / 确认 / 租约 / 关闭
├── mcbews:packet_v2     → 重组 FRAME → V2Endpoint
└── 已退休标识            → 只记录拒绝

V2Endpoint
├── CapabilityRegistry   → registerCapability / 按名分发
└── 流处理器             → registerV2Stream
```

路由器要等世界加载完成后才会激活。激活前收到的 hello 只会保留一次经过清洗的尝试，
其他在就绪前到达的业务消息会被直接丢弃，之后也不会重放。每个已就绪的对端各有自己的会话、
分片缓存和待回复请求，新对端不会顶替已有的对端。

下行消息通过 `scriptevent mcbews:packet_v2` 发送；上行则由模拟玩家 `MCBEWS_BRIDGE`
发送聊天帧。这个名字只表明消息经由哪条传输通道送达，并不是认证。

## 注册能力

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("custom.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
// unregisterCapability(registration)
```

处理器接收 `(method, payload, context)`，返回一个对象或 Promise。
上下文包含 `caller`、`requestVersion`、`metadata`、`client` 和 `cancellation`。
可选的校验器只要返回一条消息，就会拒绝这次输入。

注册同名能力默认会抛出错误，传入 `replace: true` 才会覆盖。调用进行期间注销，不会影响这次调用；
此后的调用则再也找不到这个名称。

## 注册流

```typescript
import { registerV2Stream } from "./bridge/router";

const unregister = registerV2Stream("custom.ticks", async function* (_method, params, context) {
  for (let index = 0; index < (params.count ?? 5); index += 1) {
    if (context.cancellation?.cancelled) return;
    yield { index };
  }
});
```

生产者逐项产生 JSON，不需要预先知道总长度。较大的数据项由 SDK 分片传输，重组后只交付一次。
重复注册同名的流会抛出错误。消费者通过取消来停止一条流，因此生产者必须检查 `context.cancellation`。

路由器还提供 `getV2Endpoint(epoch)`，供需要直接访问每条连接端点的高级宿主使用。

## 内置能力

所有包都会注册：

| 能力                     | 说明                                         |
| ------------------------ | -------------------------------------------- |
| `get_player_snapshot`    | 玩家名称、生命值、标签、坐标、维度与游戏模式 |
| `get_inventory_snapshot` | 背包槽位、物品 ID、数量与自定义名称          |

默认演示构建额外注册：

| 能力 / 流           | 说明                                 |
| ------------------- | ------------------------------------ |
| `demo.echo`         | 回显载荷与请求元数据                 |
| `demo.ticks`        | 有限或无限的索引流；取消后停止生产   |
| `run_world_command` | 世界命令处理器；命令禁止列表仍然生效 |

`find_entities` 等能力不随包提供。自定义的实体查询由宿主自行定义，并自行校验查询范围。

## 世界命令安全

命令需要以某名玩家为上下文时，用 `{"command": "testfor @s", "player": "Alex"}` 调用
`run_world_command`。`player` 必须是在线玩家的完整名称，附加包据此确定执行者、位置和维度；
该玩家不在线时会返回错误。省略 `player` 时，仍使用主世界维度的上下文。
示例中的 `!cmd` 会自动传入聊天发送者。

`successCount` 表示命令成功生效的次数，为零时也会明确显示。桥接调用完成，并不代表世界一定发生了变化。

`run_world_command` 随默认包发布，但安全策略依然生效：

- 默认只使用命令禁止列表：命中列表的命令入口（`execute`、`script`、`op`、
  `setblock`、`fill` 等）一律拒绝；
- 需要更严格时，可以向 `handleRunWorldCommand` 传入明确的允许列表；
- 每次调用仍然必须由 Python 宿主授权。

桥接层**不是**安全边界。传输归属、模拟玩家和元数据里的身份，都不能代替认证与授权。
附加包只提供上面这层防御性的命令过滤。

## 排查

| 现象                                          | 检查                                                                      |
| --------------------------------------------- | ------------------------------------------------------------------------- |
| 协议协商超时                                  | 附加包没有启用、进入了错误的世界，或者没有开启 **测试版 API**             |
| 日志出现 `UNSUPPORTED_PROTOCOL`               | 附加包收到了已退休的 scriptevent；请把 Python 宿主或附加包升级到 MCBEWS/2 |
| `!echo` / `!stream` 返回 `UNSUPPORTED_METHOD` | 安装的是 `--base` 精简包，请改用默认的演示构建                            |
| `!cmd` 被拒绝                                 | 命令在禁止列表中，或者安装的是 `--base` 精简构建                          |
| 游戏内没有桥接活动                            | 脚本没有加载；请检查测试版 API、内容日志和附加包是否已启用                |
| `demo.ticks` 提前停止                         | 消费者取消了这条流，或超出了流的限制                                      |

## 许可证

MIT

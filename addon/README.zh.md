[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)

# MCBE WebSocket 桥接附加包

`mcbe-ws-sdk` 的 TypeScript 对应实现，负责 MCBEWS/2 的 Minecraft 侧：
准入、数据包载体、能力注册表与请求流。Python 宿主实现另一侧。

附加包**只支持 MCBEWS/2**，只接受两个 scriptevent 标识：

- `mcbews:negotiate_v2` —— 准入控制；
- `mcbews:packet_v2` —— 逻辑消息载体。

已退休的 `mcbews:bridge_req`、`mcbews:session_req`、`mcbews:text_resp`
只记录并拒绝，不解析、不排队、不回复、不分发。附加包无法关闭旧服务持有的 WebSocket，
只拒绝其业务帧。

## 环境要求

- 支持 Script API 的 Minecraft 基岩版，且目标世界开启 **实验 → 测试版 API（Beta APIs）**。
- 构建需要 Node.js 22。
- `package.json` 中固定的依赖：
  - `@minecraft/server` 2.8.0
  - `@minecraft/server-ui` 2.1.0
  - `@minecraft/server-gametest` `1.0.0-beta.1.26.30-stable`
- 两个包清单的 `min_engine_version` 均为 `[1, 26, 30]`。

自动化构建与测试不等于实机支持。部署前请核对游戏版本与平台。

## 构建

```bash
npm install
npm run build       # tsc 类型检查 + esbuild 打包
npm run mcaddon     # 组装 .mcaddon
```

基础包只包含两个只读快照能力。

完整演示包额外包含 `demo.echo`、`demo.ticks` 流与受限的 `run_world_command`：

```bash
npm run build:demo
npm run mcaddon:demo
```

`--demo` 或 `MCBE_DEMO=1` 选择 `scripts/main.demo.ts`；两个变体都输出同一个最终文件
`dist/scripts/main.js`。`lib/` 只是类型检查的中间产物，不会由游戏加载。

打包会把 `@minecraft/server`、`@minecraft/server-ui` 与
`@minecraft/server-gametest` 作为外部模块；模块版本由包清单提供。

## 在世界中启用

1. 将生成的 `.mcaddon` 导入 Minecraft，或手动复制行为包 / 资源包。
2. 打开**目标世界**设置（新建世界，或对已有世界点**编辑**）。
3. 在**实验**（Experiments）中开启 **测试版 API（Beta APIs）**。
4. 启用行为包（如有提示，也启用配套资源包）。
5. 进入世界后执行 `/wsserver <主机IP>:8080`。

未开启 Beta APIs 时脚本不会加载：准入会超时，游戏内也看不到桥接活动。
开启实验性玩法可能使世界无法解锁部分成就；建议使用专用测试世界。

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

路由器只在世界就绪后激活。激活前到达的 hello 只保留一个经过清洗的尝试；
其他就绪前业务直接丢弃，不重放。每个已准入对端有自己的代次、分片与等待项，
新对端不会替换其他对端。

下行使用 `scriptevent mcbews:packet_v2`；上行由模拟玩家 `MCBEWS_BRIDGE`
发送聊天帧。该名称只是传输归属，不是认证。

## 注册能力

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("demo.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
// unregisterCapability(registration)
```

处理器接收 `(method, payload, context)`，返回对象或 Promise。
上下文包含 `caller`、`requestVersion`、`metadata`、`client` 与 `cancellation`。
可选校验器返回消息即拒绝输入。

重名默认抛错，传入 `replace: true` 才覆盖。调用进行中注销不影响当前调用；
之后的调用不再看到该名称。当前版本没有聚合的 `setCapabilityHandler` 入口。

## 注册流

```typescript
import { registerV2Stream } from "./bridge/router";

const unregister = registerV2Stream("demo.ticks", async function* (_method, params, context) {
  for (let index = 0; index < (params.count ?? 5); index += 1) {
    if (context.cancellation?.cancelled) return;
    yield { index };
  }
});
```

生产者逐项产生 JSON，不需要预先知道总长度。大项由 SDK 分片，重组后只交付一次。
重复注册同名流会抛错。消费者通过取消停止；生产者必须检查 `context.cancellation`。

路由器还提供 `getV2Endpoint(epoch)`，供需要直接访问每连接端点的高级宿主使用。

## 内置能力

基础包只注册：

| 能力 | 说明 |
| --- | --- |
| `get_player_snapshot` | 玩家名称、生命值、标签、坐标、维度与游戏模式 |
| `get_inventory_snapshot` | 背包槽位、物品 ID、数量与自定义名称 |

完整演示包额外注册：

| 能力 / 流 | 说明 |
| --- | --- |
| `demo.echo` | 回显载荷与请求元数据 |
| `demo.ticks` | 有限或无限的索引流；取消后停止生产 |
| `run_world_command` | 受限的世界命令处理器，基础包不提供 |

`find_entities` 等能力不随包提供。自定义实体查询由宿主定义，并自行校验范围。

## 世界命令安全

`run_world_command` 故意不在基础包中。完整演示包会显式注册它，但策略仍然生效：

- 默认允许列表只有 `["say"]`；
- 危险命令禁止列表（`execute`、`script`、`op`、`setblock`、`fill` 等）
  是第二道防线，即使宿主放宽允许列表也仍然生效；
- 每次调用仍须由 Python 宿主授权。

桥接层**不是**安全边界。传输归属、模拟玩家与元数据身份都不能替代认证与授权。
附加包只提供上述防御性命令过滤。

## 排查

| 现象 | 检查 |
| --- | --- |
| 准入超时 | 包未启用、目标世界不对，或 **测试版 API** 未开启 |
| 日志出现 `UNSUPPORTED_PROTOCOL` | 有已退休的 scriptevent 到达附加包；把 Python 宿主或附加包升级到 MCBEWS/2 |
| `!echo` / `!stream` 返回 `UNSUPPORTED_METHOD` | 安装的是基础包，请使用演示构建 |
| `!cmd` 被拒绝 | 未注册 `run_world_command`，或命令不在允许列表 |
| 游戏内没有桥接活动 | 脚本未加载；确认测试版 API、内容日志与包启用情况 |
| `demo.ticks` 提前停止 | 消费者取消，或超过流限制 |

## 许可证

MIT

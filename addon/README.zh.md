[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)

# MCBE WS Bridge Addon

极简的 Minecraft Bedrock WebSocket 桥接 addon —— `mcbe-ws-sdk` Python 包的 TypeScript 对等实现。本 addon 负责协议层：

- `constants.ts` — 与 Python `AddonProtocolConfig` 对齐的 wire protocol 常量
- `chunking.ts` — `MCBEAI|RESP` 与 `MCBEAI|UI_CHAT` 分片的编解码
- `router.ts` — 订阅 `mcbeai:bridge_request` scriptevent，调用宿主注册的处理器，并将响应回送
- `responseSync.ts` — 重组 `mcbeai:ai_resp` 分片并触发回调
- `toolPlayer.ts` — 为每次响应投递（tellraw）标记一个“塔式传输”玩家
- `bootstrap.ts` — 安全的初始化生命周期（早期初始化 → 世界加载完成后）

宿主注册自身的能力处理器（`setCapabilityHandler`）、响应发送器（`setResponseSender`）以及 AI 响应处理器（`setAiRespHandler`）。Addon 仅提供传输层；对载荷的处理决策由宿主决定。

## 用法

从 Python 宿主出发，`McbeServerFacade` + `AddonBridgeService` 发送能力请求与 AI 响应；addon 接收并在游戏内投递。

从 TypeScript 宿主出发：

```ts
import { setCapabilityHandler, setResponseSender } from "./bridge/router";
import { setAiRespHandler } from "./bridge/responseSync";
import { sendBridgeResponseChunks } from "./bridge/toolPlayer";

setResponseSender((requestId, body) => sendBridgeResponseChunks(requestId, body));

setCapabilityHandler((capability, payload) => {
  // 按名称分发；返回 { ok: true, ... } 或 { ok: false, error: "..." }
});

setAiRespHandler((playerName, role, text) => {
  // 展示重组后的 AI 响应
});
```

然后在 `main.ts` 中：

```ts
import { initializeEarly, initializeAfterWorldLoad } from "./bootstrap";

initializeEarly();
initializeAfterWorldLoad(() => { /* 在此注册处理器 */ });
```

## 构建

```bash
npm install
npm run build       # tsc 类型检查门卫（中间产物输出到 lib/）+ esbuild 打包
npm run mcaddon     # 在构建产物基础上组装 .mcaddon
```

构建流水线分两步：

1. **`tsc` 类型检查门卫**：对源码做类型校验，中间产物输出到 `lib/`（仅用于类型检查，并非最终产物）。
2. **esbuild `bundle` 任务**：产出真正的最终产物 `dist/scripts/main.js`，随后由 `copyArtifacts` 将 `dist/scripts` 复制进 behavior_pack，与 manifest 的 `"entry": "scripts/main.js"` 对应。

最终在游戏内加载的是 `scripts/main.js`，请以此为准，而非 `lib/` 下的中间产物。

## 内置基础能力

Addon 内置一套开箱即用的基础能力注册表（`scripts/bridge/capabilities/index.ts`），
作为可运行的参考示例。当宿主未通过 `setCapabilityHandler` 注入自定义处理器时，
`router.ts` 会回退到该注册表；一旦宿主注册了处理器，则完全覆盖默认表。

| 能力名 | 文件 | 说明 |
| --- | --- | --- |
| `get_player_snapshot` | `getPlayerSnapshot.ts` | 获取玩家快照（名称、生命值、标签、坐标、维度、游戏模式） |
| `get_inventory_snapshot` | `getInventorySnapshot.ts` | 获取玩家背包快照（槽位、物品 ID、数量、自定义名称） |
| `run_world_command` | `runWorldCommand.ts` | 在世界上执行一条 MC 命令（受 `commandSafety.ts` 黑名单保护） |

开发者添加自定义能力有两种方式：

1. 实现一个 `CapabilityHandler`（`(capability, payload) => { ok, payload }`），并通过
   `setCapabilityHandler` 注册——这会覆盖默认注册表，完全由宿主接管分发。
2. 在 `capabilities/index.ts` 的 `defaultCapabilityRegistry` 中追加新条目，
   与内置能力共存，仍由 `router.ts` 按能力名查找调用。

> 注意：`find_entities` 暂未内置。当前协议中的 `scriptevent` 并不携带发起玩家的
> 来源上下文（source-player），而实体查询需要该上下文来限定范围；一旦协议支持
> 玩家来源的 scriptevent，该能力即会补充进来。

## 许可证

MIT

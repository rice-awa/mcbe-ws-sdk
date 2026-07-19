# MCBE WS Bridge Addon

Minimal Minecraft Bedrock WebSocket bridge addon — the TypeScript counterpart of the
`mcbe-ws-sdk` Python package. This addon handles the protocol layer:

- `constants.ts` — wire protocol constants matching Python `AddonProtocolConfig`
- `chunking.ts` — encode/decode for `MCBEAI|RESP` and `MCBEAI|UI_CHAT` fragments
- `router.ts` — subscribes to `mcbeai:bridge_request` scriptevents, calls a host-registered handler, ships the response back
- `responseSync.ts` — reassembles `mcbeai:ai_resp` chunks and invokes a callback
- `toolPlayer.ts` — marks a player as a towered transport per response delivery (tellraw)
- `bootstrap.ts` — safe init lifecycle (early init → after-world-load)

The host registers its own capability handler (`setCapabilityHandler`),
response sender (`setResponseSender`), and AI response handler
(`setAiRespHandler`). The addon provides only the transport; decisions about
what to do with the payload are the host's.

## Usage

From a Python host, `McbeServerFacade` + `AddonBridgeService` sends capability
requests and AI responses; the addon picks them up and delivers them in-game.

From a TypeScript host:

```ts
import { setCapabilityHandler, setResponseSender } from "./bridge/router";
import { setAiRespHandler } from "./bridge/responseSync";
import { sendBridgeResponseChunks } from "./bridge/toolPlayer";

setResponseSender((requestId, body) => sendBridgeResponseChunks(requestId, body));

setCapabilityHandler((capability, payload) => {
  // dispatch by name; return { ok: true, ... } or { ok: false, error: "..." }
});

setAiRespHandler((playerName, role, text) => {
  // display the reassembled AI response
});
```

Then in `main.ts`:

```ts
import { initializeEarly, initializeAfterWorldLoad } from "./bootstrap";

initializeEarly();
initializeAfterWorldLoad(() => { /* register handlers here */ });
```

## Build

```bash
npm install
npm run build       # tsc 类型检查门卫（中间产物输出到 lib/）+ esbuild 打包
npm run mcaddon     # 在 build 基础上组装 .mcaddon
```

构建流水线分两步：

1. **`tsc` 类型检查门卫**：对源码做类型校验，中间产物输出到 `lib/`（仅用于类型检查，不是最终产物）。
2. **esbuild `bundle` 任务**：产出真正的最终产物 `dist/scripts/main.js`，随后由 `copyArtifacts` 将 `dist/scripts` 复制进 behavior_pack，与 manifest 的 `"entry": "scripts/main.js"` 对应。

最终游戏内加载的是 `scripts/main.js`，请以此为准，而非 `lib/` 下的中间产物。

## 内置基础能力

Addon 内置了一套开箱即用的基础能力注册表（`scripts/bridge/capabilities/index.ts`），
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

## License

MIT

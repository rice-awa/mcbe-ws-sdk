[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)

# MCBE WS Bridge Addon

极简的 Minecraft Bedrock WebSocket 桥接 addon —— `mcbe-ws-sdk` Python 包的 TypeScript 对等实现。本 addon 负责协议层：

- `constants.ts` — 与 Python `McbewsV1Profile` 对齐的 wire protocol 常量
- `chunking.ts` — `MCBEWS|BRIDGE` 与 `MCBEWS|UI_CHAT` 分片的编解码
- `router.ts` — 订阅 `mcbews:bridge_req` scriptevent，分发到内置能力注册表并将响应回送
- `responseSync.ts` — 重组 `mcbews:text_resp` 分片并触发回调
- `toolPlayer.ts` — 为每次响应投递（tellraw）标记一个"塔式传输"玩家
- `bootstrap.ts` — 安全的初始化生命周期（早期初始化 → 世界加载完成后）

Addon 完全拥有能力处理生命周期。能力通过 `capabilities/index.ts` 中的内置
能力注册表进行分发。Python SDK 不包含入站能力注册表 —— 它发送请求
并接收响应。

## 用法

从 Python 宿主出发，`AddonBridgeService` + `AddonBridgeClient` 发送能力请求；
addon 接收并在游戏内投递。

从 addon 侧出发，通过在 `capabilities/index.ts` 的能力注册表中
追加条目来注册能力。路由器会自动分发。

### TypeScript 宿主（覆盖模式）

当 TypeScript 宿主需要完全控制时，可以覆盖内置注册表：

```ts
import { setCapabilityHandler } from "./bridge/router";

setCapabilityHandler((capability, payload) => {
  // 按名称分发；返回 { ok: true, ... } 或 { ok: false, error: "..." }
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

## 内置能力

Addon 内置一套开箱即用的能力注册表（`scripts/bridge/capabilities/index.ts`），
作为可运行的参考示例。

| 能力名 | 文件 | 说明 |
| --- | --- | --- |
| `get_player_snapshot` | `getPlayerSnapshot.ts` | 获取玩家快照（名称、生命值、标签、坐标、维度、游戏模式） |
| `get_inventory_snapshot` | `getInventorySnapshot.ts` | 获取玩家背包快照（槽位、物品 ID、数量、自定义名称） |
| `run_world_command` | `runWorldCommand.ts` | 在世界上执行一条 MC 命令（受 `commandSafety.ts` 黑名单保护） |

开发者通过在 `capabilities/index.ts` 的能力注册表中追加新条目来
添加自定义能力，与内置能力共存，仍由 `router.ts` 按能力名查找调用。

> 注意：`find_entities` 暂未内置。当前协议中的 `scriptevent` 并不携带发起玩家的
> 来源上下文（source-player），而实体查询需要该上下文来限定范围；一旦协议支持
> 玩家来源的 scriptevent，该能力即会补充进来。

## 信任边界

桥接层**不是**安全边界。宿主应用必须自行认证 / 授权谁可以调用能力 ——
addon 仅在世界命令路径上提供防御性命令过滤。

- `run_world_command` 故意**不**出现在 Python 默认注册表中（也不在 addon
  默认能力注册表中）。需要该能力的宿主必须显式注册 handler。
- 注册后，默认 allowlist 仅为 `["say"]`。危险命令 denylist
  （`execute`、`script`、`op`、`setblock`、`fill` 等）是第二道防线，
  即便宿主放宽 allowlist 仍然生效。

## 许可证


MIT

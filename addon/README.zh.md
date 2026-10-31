[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)

# MCBE WebSocket 桥接附加包

当前仓库包含尚未发布的 v2、元数据与流式能力，不能视为已随 PyPI `0.2.1` 发布。
`scripts/bridge/protocol.ts` 与 Python 安装包的清单及测试向量保持一致；新旧协议分别维护合同。
新宿主使用 `McbeServer`，旧 `McbeServerFacade` 默认值不变。
参见[扩展作者指南](../docs/extension-authors.zh.md)与[迁移指南](../docs/migration.zh.md)。
实机与完整兼容矩阵尚待验收，不扩大平台或版本支持承诺。

这是 `mcbe-ws-sdk` Python 包配套的 TypeScript 桥接附加包，负责以下协议工作：

- `protocol.ts` — SDK 清单、版本、限制、错误与测试向量的生成映射
- `constants.ts` — 与 Python `McbewsV1Profile` 对齐的协议常量
- `chunking.ts` — `MCBEWS|BRIDGE` 与 `MCBEWS|UI_CHAT` 分片的编解码
- `router.ts` — 订阅 `mcbews:bridge_req` scriptevent，分发到内置能力注册表并将响应回送
- `responseSync.ts` — 重组 `mcbews:text_resp` 分片并触发回调
- `toolPlayer.ts` — 为每次响应投递（tellraw）标记一个"塔式传输"玩家
- `bootstrap.ts` — 安全的初始化生命周期（早期初始化 → 世界加载完成后）

附加包负责能力处理生命周期。能力通过 `capabilities/index.ts` 中的注册表
按名称分发。显式启用 v2 桥后，附加包 → Python 请求和通知也会经 Python 注册表分发。
探测与回退规则见 [v2 合同](../docs/protocol-v2.zh.md)。

## 用法

从 Python 宿主出发，`AddonBridgeService` + `AddonBridgeClient` 发送能力请求；
附加包接收并在游戏内投递。

在附加包中，按能力名注册处理器。自定义能力与内置能力共存，
路由器自动分发：

```ts
import { registerCapability } from "./bridge/router";

const handle = registerCapability("echo", (_name, payload) => ({ ok: true, payload }));
// unregisterCapability(handle)
```

重名默认抛错，传入 `replace: true` 才覆盖。可选 `validate(payload)` 在处理器之前
运行，返回消息即拒绝输入。调用进行中注销不影响当前调用；之后的调用不再看到该名称。

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
initializeAfterWorldLoad(() => {
  /* 在此注册处理器 */
});
```

## 构建

```bash
npm install
npm run build       # tsc 类型检查（中间产物输出到 lib/）+ esbuild 打包
npm run mcaddon     # 在构建产物基础上组装 .mcaddon
```

构建流水线分两步：

1. **`tsc` 类型检查**：对源码做类型校验，中间产物输出到 `lib/`（仅用于类型检查，并非最终产物）。
2. **esbuild `bundle` 任务**：产出真正的最终产物 `dist/scripts/main.js`，随后由 `copyArtifacts` 将 `dist/scripts` 复制进行为包，与清单的 `"entry": "scripts/main.js"` 对应。

最终在游戏内加载的是 `scripts/main.js`，请以此为准，而非 `lib/` 下的中间产物。

## 在世界中启用

1. 将生成的 `.mcaddon` 导入 Minecraft（或手动复制行为包 / 资源包）。
2. 打开**目标世界**的设置（新建世界，或对已有世界点**编辑**）。
3. 在 **实验**（Experiments）里打开 **测试版 API**（Beta APIs）。
4. 在 **行为包** 中启用本包（如提示配套资源包也一并启用）。
5. 进入世界后，再执行 `/wsserver <主机IP>:8080` 连接。

> **必开：测试版 API**
> 本附加包依赖 Minecraft Script API（`@minecraft/server`、
> `@minecraft/server-ui`，以及 测试版的 `@minecraft/server-gametest`）。
> 若未开启 **实验 → 测试版 API**，脚本不会加载：能力请求会超时，
> 游戏内也看不到任何桥接活动。
> 开启实验性玩法可能导致该世界无法解锁部分成就 / 功能，建议使用专用测试世界。

## 内置能力

附加包内置一套开箱即用的能力注册表（`scripts/bridge/capabilities/index.ts`），
作为可运行的参考示例。

| 能力名                   | 文件                      | 说明                                                         |
| ------------------------ | ------------------------- | ------------------------------------------------------------ |
| `get_player_snapshot`    | `getPlayerSnapshot.ts`    | 获取玩家快照（名称、生命值、标签、坐标、维度、游戏模式）     |
| `get_inventory_snapshot` | `getInventorySnapshot.ts` | 获取玩家背包快照（槽位、物品 ID、数量、自定义名称）          |
| `run_world_command`      | `runWorldCommand.ts`      | 可选的世界命令处理器，须显式注册，受 `commandSafety.ts` 限制 |

开发者通过 `registerCapability(name, handler)` 添加自定义能力，
与内置能力共存，仍由 `router.ts` 按能力名查找调用。
源码中的 `capabilities/index.ts` 表仍可用于编译期追加；运行时新增能力名需使用
`registerCapability`。

> 注意：`find_entities` 暂未内置。传输来源与元数据身份不能替代玩家授权；
> 自定义实体查询需要由宿主定义并校验调用范围。

## 信任边界

桥接层**不是**安全边界。宿主应用必须自行认证 / 授权谁可以调用能力 ——
附加包仅在世界命令路径上提供防御性命令过滤。

- `run_world_command` 故意**不**出现在 Python 默认注册表中（也不在附加包
  默认能力注册表中）。需要该能力的宿主必须显式注册处理器。
- 注册后，默认允许列表 仅为 `["say"]`。危险命令禁止列表
  （`execute`、`script`、`op`、`setblock`、`fill` 等）是第二道防线，
  即便宿主放宽允许列表 仍然生效。

## 许可证

MIT

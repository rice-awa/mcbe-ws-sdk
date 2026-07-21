# Addon 能力演示服务器

基于 `mcbe-ws-sdk` 的可运行 Minecraft Bedrock WebSocket 示例，通过聊天命令调用
bridge addon 的内置能力。

它会：

1. 监听 `0.0.0.0:8080`；
2. 接受 Minecraft Bedrock 的 `/wsserver` 连接；
3. 订阅 `PlayerMessage`；
4. 通过 `AddonBridgeService` 发起能力请求；
5. 用 `McbeOutboundDelivery` 把格式化结果以 `tellraw` 回给玩家。

## 前置条件

1. 在仓库根目录安装 SDK（推荐开发模式）：

   ```bash
   pip install -e ".[dev]"
   ```

2. 构建并在目标世界中启用 bridge addon：

   ```bash
   cd addon
   npm install
   npm run build
   npm run mcaddon
   ```

   把生成的 `.mcaddon`（或行为包）导入世界，并确保该包处于启用状态。

   **必开：** 在世界设置的 **实验** 中启用 **测试版 API**（Beta APIs）。
   未开启时 Script API 模块不会加载，桥接能力请求会超时且游戏内无任何效果。
   建议使用专用测试世界（实验性玩法可能影响成就 / 功能）。

3. 启动本示例后，在游戏中执行：

   ```text
   /wsserver <运行 Python 服务端的机器 IP>:8080
   ```

## 运行

```bash
python examples/addon-server/server.py
python examples/addon-server/server.py --host 0.0.0.0 --port 8080
python examples/addon-server/server.py --log-level DEBUG
```

## 聊天命令

| 命令 | 能力 | 说明 |
| --- | --- | --- |
| `!player [target]` / `!玩家 [target]` | `get_player_snapshot` | 默认目标为发送者本人 |
| `!inv [target]` / `!背包 [target]` | `get_inventory_snapshot` | 默认目标为发送者本人 |
| `!cmd <command>` / `!命令 <command>` | `run_world_command` | **默认未注册**，见下文 |
| `!wscmd <command>` / `!ws命令 <command>` | WS `commandRequest` | 不依赖 addon；由 host 跟踪 `commandResponse` |
| `!help` / `帮助` | — | 显示帮助 |

示例：

```text
!player
!player Steve
!inv
!cmd time query daytime
!wscmd time query daytime
!help
```

## 启用 `run_world_command`

handler 位于 `addon/scripts/bridge/capabilities/runWorldCommand.ts`，并已导出，
但**故意不在**默认注册表中（默认只有 `get_player_snapshot` /
`get_inventory_snapshot`）。要让本示例的 `!cmd` 可用，任选其一：

1. 在 `capabilities/index.ts` 的默认注册表中追加：

   ```ts
   import { handleRunWorldCommand } from "./runWorldCommand";

   export const defaultCapabilityRegistry: Record<string, CapabilityHandler> = {
     get_player_snapshot: (_c, payload) => handleGetPlayerSnapshot(payload),
     get_inventory_snapshot: (_c, payload) => handleGetInventorySnapshot(payload),
     run_world_command: (_c, payload) => handleRunWorldCommand(payload),
   };
   ```

2. 或在 TypeScript 宿主侧覆盖 handler：

   ```ts
   import { setCapabilityHandler } from "./bridge/router";
   import { handleRunWorldCommand } from "./bridge/capabilities";

   setCapabilityHandler((capability, payload, context) => {
     if (capability === "run_world_command") {
       return handleRunWorldCommand(payload);
     }
     // 其余能力按需回退到默认表或其他 handler
     ...
   });
   ```

改完后重新构建 addon。`commandSafety.ts` 中的黑名单仍会拦截
`stop` / `reload` / `kick` / `op` / `deop`。

## 桥接调用链路

```text
玩家聊天
  → AddonDemoHook
  → AddonBridgeService.create_client(...).request(capability, payload)
  → McbeOutboundDelivery.send_raw_command("scriptevent mcbews:bridge_req ...")
  → addon router 处理能力
  → bridge tool player 以聊天分片回传
  → facade 把分片交给 AddonBridgeService
  → 挂起的 future 完成
  → tellraw 摘要回给玩家
```

## WS 侧 `!wscmd` 调用链路

SDK 可以**发送** `commandRequest`（`McbeOutboundDelivery.send_raw_command`），
并把匹配的 `commandResponse` **交给** `ConnectionHook.on_command_response`，
但故意**不**维护 pending future 表——关联由 host 负责。本示例在
`WsCommandRunner` 中展示最小模式：

```text
!wscmd time query daytime
  → on_player_message 调度后台 task（绝不能阻塞接收循环）
  → WsCommandRunner.run()
  → send_raw_command(..., before_send=按 requestId 登记 future)
  → Bedrock 返回 commandResponse
  → facade → on_command_response → WsCommandRunner.resolve()
  → future 完成 → tellraw statusCode / statusMessage
```

**不要在 `on_player_message` 里直接 `await runner.run()`（或 addon
`client.request()`）。** facade 用
`async for raw in websocket: await _handle_raw(...)` 处理入站帧，并会 await
hook。若在这里阻塞等待 `commandResponse` / bridge 聊天分片，接收循环会被饿死：
游戏侧命令可能已执行，但 Python 侧会一直超时。本示例用
`asyncio.create_task(...)` 让 hook 立即返回。

只需要普通 Minecraft 命令、不想依赖 bridge addon 时用 `!wscmd`；
需要 addon 的 `run_world_command` 路径（黑名单、Script API 副作用等）时用 `!cmd`。

要点：

- hook（出站请求）与 facade（入站重组）共享同一个 `AddonBridgeService` 实例，
  这样 pending future 在同一 session map 上。
- 用 `player_event.sender` 作为回复目标。不要使用已弃用的连接级
  `ConnectionState.player_name` 作为会话身份——一条 `/wsserver` 连接可能承载多名玩家。
- Bedrock 会把 tellraw 回显成 `sender=外部` / `External`；示例会忽略这些回显。
- 未跟踪的 `commandResponse`（我们自己的 tellraw/scriptevent 确认）会被忽略，
  除非 `statusCode` 非 0。

## 注意事项

- Minecraft 跑在另一台机器时，使用服务端局域网 IP，不要用 `127.0.0.1`。
- 防火墙需放行 TCP `8080` 入站。
- 示例没有认证，只建议在可信本机/局域网环境使用。
- 若请求超时，按顺序检查：(1) 本世界已启用 bridge 行为包；(2) **实验 → 测试版 API**
  已打开（否则脚本不会加载）；(3) `/wsserver` 连接仍然有效。

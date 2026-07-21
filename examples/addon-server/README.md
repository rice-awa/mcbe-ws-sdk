# Addon Capability Demo Server

A runnable Minecraft Bedrock WebSocket server built on `mcbe-ws-sdk` that
exercises the bridge addon's built-in capabilities through chat commands.

It:

1. listens on `0.0.0.0:8080`;
2. accepts Minecraft Bedrock `/wsserver` connections;
3. subscribes to `PlayerMessage`;
4. sends capability requests through `AddonBridgeService`; and
5. replies with a formatted `tellraw` summary via `McbeOutboundDelivery`.

## Prerequisites

1. Install the SDK (editable) from the repository root:

   ```bash
   pip install -e ".[dev]"
   ```

2. Build and enable the bridge addon in the target world:

   ```bash
   cd addon
   npm install
   npm run build
   npm run mcaddon
   ```

   Then import the produced `.mcaddon` (or the behavior pack) into the world
   and make sure the pack is active.

   **Required:** in the world settings, open **Experiments** and enable
   **Beta APIs**. Without it, Script API modules do not load — bridge
   capability calls will time out with no in-game effect. Prefer a dedicated
   test world (experiments can affect achievements / features).

3. Start this server, then in-game run:

   ```text
   /wsserver <machine-running-the-server>:8080
   ```

## Run

```bash
python examples/addon-server/server.py
python examples/addon-server/server.py --host 0.0.0.0 --port 8080
python examples/addon-server/server.py --log-level DEBUG
```

## Chat commands

| Command | Capability | Notes |
| --- | --- | --- |
| `!player [target]` / `!玩家 [target]` | `get_player_snapshot` | Default target is the sender |
| `!inv [target]` / `!背包 [target]` | `get_inventory_snapshot` | Default target is the sender |
| `!cmd <command>` / `!命令 <command>` | `run_world_command` | **Not registered by default** — see below |
| `!wscmd <command>` / `!ws命令 <command>` | WS `commandRequest` | No addon required; host tracks `commandResponse` |
| `!help` / `帮助` | — | Show help |

Examples:

```text
!player
!player Steve
!inv
!cmd time query daytime
!wscmd time query daytime
!help
```

## Enabling `run_world_command`

The handler exists in `addon/scripts/bridge/capabilities/runWorldCommand.ts`
and is exported, but it is intentionally **not** part of the default registry
(`get_player_snapshot` / `get_inventory_snapshot` only). To enable it for this
demo, either:

1. append it to the default registry in `capabilities/index.ts`:

   ```ts
   import { handleRunWorldCommand } from "./runWorldCommand";

   export const defaultCapabilityRegistry: Record<string, CapabilityHandler> = {
     get_player_snapshot: (_c, payload) => handleGetPlayerSnapshot(payload),
     get_inventory_snapshot: (_c, payload) => handleGetInventorySnapshot(payload),
     run_world_command: (_c, payload) => handleRunWorldCommand(payload),
   };
   ```

2. or override the handler from a TypeScript host:

   ```ts
   import { setCapabilityHandler } from "./bridge/router";
   import { handleRunWorldCommand } from "./bridge/capabilities";

   setCapabilityHandler((capability, payload, context) => {
     if (capability === "run_world_command") {
       return handleRunWorldCommand(payload);
     }
     // fall through to other handlers / default registry as needed
     ...
   });
   ```

Rebuild the addon after changing the registry. The denylist in
`commandSafety.ts` still blocks `stop` / `reload` / `kick` / `op` / `deop`.

## How the bridge call is wired

```text
player chat
  → AddonDemoHook
  → AddonBridgeService.create_client(...).request(capability, payload)
  → McbeOutboundDelivery.send_raw_command("scriptevent mcbews:bridge_req ...")
  → addon router handles capability
  → bridge tool player replies via chat chunks
  → facade routes chunks into AddonBridgeService
  → pending future resolves
  → tellraw summary back to the player
```

## How WS-side `!wscmd` is wired

The SDK can **send** a `commandRequest` (`McbeOutboundDelivery.send_raw_command`)
and **deliver** the matching `commandResponse` to
`ConnectionHook.on_command_response`, but it intentionally does **not** keep a
pending-future map — that correlation is host-owned. This example shows the
minimal pattern in `WsCommandRunner`:

```text
!wscmd time query daytime
  → on_player_message schedules a background task (must not block the receive loop)
  → WsCommandRunner.run()
  → send_raw_command(..., before_send=register future by requestId)
  → Bedrock replies with commandResponse
  → facade → on_command_response → WsCommandRunner.resolve()
  → future completes → tellraw statusCode / statusMessage
```

**Do not `await runner.run()` (or addon `client.request()`) directly inside
`on_player_message`.** The facade processes inbound frames with
`async for raw in websocket: await _handle_raw(...)`, which awaits the hook.
Blocking there for a `commandResponse` / bridge chat chunk starves the receive
loop: the game may still execute the command, but the Python side times out
forever. This example uses `asyncio.create_task(...)` so the hook returns
immediately.

Use `!wscmd` when you only need a normal Minecraft command and do not want to
depend on the bridge addon. Use `!cmd` when you need the addon's
`run_world_command` path (denylist, Script API side effects, etc.).

Important details:

- One `AddonBridgeService` instance is shared by the hook (outbound) and the
  facade (inbound reassembly) so pending futures stay on the same session map.
- `player_event.sender` is the reply target. Do not use the deprecated
  connection-level `ConnectionState.player_name` as a session identity — one
  `/wsserver` connection can carry many players.
- Bedrock echoes tellraw as `sender=外部` / `External`; those echoes are ignored.
- Untracked `commandResponse` frames (our own tellraw/scriptevent acks) are
  ignored unless `statusCode` is non-zero.

## Notes

- Use the server machine's LAN IP when Minecraft runs elsewhere; do not use
  `127.0.0.1` in that case.
- Allow TCP port `8080` through the host firewall.
- This example has no authentication. Use it only on a trusted local/LAN
  network.
- If a request times out, check in order: (1) the bridge addon pack is active
  in this world; (2) **Experiments → Beta APIs** is on (scripts do not load
  otherwise); (3) the `/wsserver` connection is still up.

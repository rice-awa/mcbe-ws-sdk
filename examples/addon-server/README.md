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

`ConnectionState.command_client` is an SDK-owned `NativeCommandClient`. It
registers the request *before* sending and pairs the matching `commandResponse`
through the shared request table, so the host keeps no wait table:

```text
!wscmd time query daytime
  → on_player_message awaits state.command_client.run(command)
  → send_raw_command(...) registers requestId, then sends
  → Bedrock replies with commandResponse
  → facade resolves the pending request, then calls on_command_response
  → run() returns the typed response → tellraw statusCode / statusMessage
```

`on_command_response` is still invoked for every response (the SDK resolves its
own request first), so hosts can keep observing acks.

### Reliable vs legacy callback behavior

This example enables the reliable path explicitly:

```python
GatewaySettings(dispatch=DispatchSettings(mode="reliable"))
```

- **Reliable** (`mode="reliable"`): the reader keeps draining frames while a
  host callback runs. A callback may `await state.command_client.run(...)` or
  `await addon_client.request(...)` directly. Work is serialised per player
  (`player:<sender>`): the same player stays FIFO and different players run
  concurrently.
- **Legacy** (the SDK default, `DispatchSettings()`): callbacks are awaited
  inline by the single read loop, exactly as before. Awaiting a reply that only
  that loop can deliver still deadlocks; schedule `asyncio.create_task(...)` or
  enable reliable mode.

The example branches on `state.runtime is not None`, so both paths work.

### Sending long text

Pass `send_bulk=state.send_bulk` to `McbeOutboundDelivery` to mark tellraw /
scriptevent chunks as bulk; raw commands stay request-class frames. On the
reliable path the scheduler keeps a bounded reservation for control frames,
sends requests ahead of bulk, and applies a fairness quota so bulk traffic is
not starved by sustained request/control load. `state.send_payload` keeps its
old single-argument contract, so existing host code is unchanged.

Use `!wscmd` when you only need a normal Minecraft command and do not want to
depend on the bridge addon. Use `!cmd` when you need the addon's
`run_world_command` path (denylist, Script API side effects, etc.).

## Recommended production settings

The SDK default remains `dispatch.mode="legacy"` so existing hosts keep their
original ordering. For a new deployment this example changes only one setting:

| Setting | Value | Why |
| --- | --- | --- |
| `dispatch.mode` | `"reliable"` | the reader no longer blocks on host callbacks |
| `dispatch.business_queue_maxsize` | `256` (default) | bounded overload; submissions are rejected explicitly |
| `dispatch.max_concurrent_business_tasks` | `64` (default) | bounded per-connection concurrency |
| `send.queue_maxsize` / `send.queue_max_bytes` | `256` / `4 MiB` (default) | bounded outbound memory |
| `send.control_reserve_slots` / `send.control_reserve_bytes` | `1` / `4096` (default) | control frames keep room while bulk is in flight |
| `send.fairness_quota` | `8` (default) | a lower priority gets one turn after 8 higher-priority frames |
| `send.capacity_timeout_seconds` | `5.0` (default) | one total admission deadline; notifications do not restart it |

These are `GatewaySettings()` defaults; enabling reliable mode is the only change
this example makes. No MCBE real-world validation has been recorded for these
values yet (tracked for R18).

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

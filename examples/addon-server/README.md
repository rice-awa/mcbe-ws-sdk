# Addon capability server

This example uses `McbeServer` from the **unreleased checkout**, not PyPI
`0.2.1`. The SDK manages callbacks, request waiters, timeouts and fragments;
hooks await command/capability calls directly.

## Run

From the repository root:

```bash
pip install -e ".[dev]"
cd addon
npm install
npm run build
npm run mcaddon
```

Import the generated `.mcaddon`. In the target world's settings enable
**Experiments → Beta APIs** and activate its behavior pack. Without Beta APIs,
scripts do not load and capability requests time out. See
[addon setup](../../addon/README.md#enable-in-a-world).

From the repository root, start Python:

```bash
python examples/addon-server/server.py
python examples/addon-server/server.py --host 0.0.0.0 --port 8080 --log-level DEBUG
```

In game: `/wsserver <python-host-ip>:8080`. Use a LAN IP for another device,
allow the listening port through the firewall, and stop Python with `Ctrl+C`.

| Chat command | Action |
| --- | --- |
| `!player [target]` | `get_player_snapshot`, current player by default |
| `!inv [target]` | `get_inventory_snapshot` |
| `!wscmd <command>` | Native `state.command_client.run`, no addon needed |
| `!cmd <command>` | Addon `run_world_command`, separately enabled handler required |
| `!echo <text>` | Custom `demo.echo`, with custom metadata |
| `!stream [count]` | Consume finite `demo.ticks` until normal completion (default 5) |
| `!stream-cancel [count]` | Consume an unbounded producer, then cancel after count items (default 3) |
| `!help` | Show command help |

## Install the optional demo handlers

Inventory and player snapshots use the built-ins. Custom echo and streams need
the included registration module in your addon composition:

1. Copy `examples/addon-server/capabilities.ts` to `addon/scripts/demoCapabilities.ts`.
2. In `addon/scripts/main.ts`, import and call the registration once during setup:

   ```typescript
   import { registerDemoCapabilities } from "./demoCapabilities";
   registerDemoCapabilities();
   ```

3. Rebuild with `npm run build` and `npm run mcaddon` in `addon/`, reimport the
   generated pack, and activate it in the target world with **Experiments →
   Beta APIs** enabled. Reconnect to negotiate a fresh session.

This registers `demo.echo` and `demo.ticks` without editing SDK protocol or
fragment logic. Do not register twice: duplicate names are rejected.
`!echo` sends `example.label` and `example.player` metadata; these values are
application context, not proof of player identity or authorization.

`run_world_command` is disabled by default. If needed, register its existing
handler explicitly through the addon router; its default allowlist is `say`
and its denylist still applies. See the [trust boundary](../../addon/README.md#trust-boundary).

## Ordering, failures and cancellation

`McbeServer` enables reliable dispatch and creates a negotiated `V2Bridge`.
Same-player callbacks remain FIFO; other players can progress independently.
The reader continues receiving replies while a hook awaits work. The example
keeps no request wait table, manual fragment state or background request tasks.

Negotiated requests return a response envelope; successful data is in `result`.
V2 remote errors raise `RemoteCallError`. V1 fallback keeps capability-level
`ok: false` inside `result`. Native replies carry `statusCode`. Timeouts,
unsupported features and transport failures end the call explicitly.

Custom metadata and streams require v2 features. An old addon only supports
known v1 methods; it cannot silently accept metadata or pretend to stream.
Streams use `async with stream` so early exit cancels the producer. Cancellation
does not undo already executed world changes. Slow consumers terminate with
bounded buffers; receiver credits are not implemented.

The old `McbeServerFacade` serial default remains unchanged. Its inline hooks
cannot await replies received by that same reader; see the [migration table](../../docs/migration.md).
That legacy limitation does not require new hosts to start their own tasks.

Use `event.sender`, not the connection's deprecated last-player pointer. The
example filters external tellraw echoes. It has no authentication: use a
trusted local/LAN environment and add host authorization for production.
Automated examples do not establish new Minecraft platform/version support;
live-game acceptance is still pending. See [extension authors](../../docs/extension-authors.md)
for registration, event subscriptions and protocol limits.

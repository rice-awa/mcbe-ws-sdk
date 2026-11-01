# Addon capability server

A `McbeServer` host that calls the companion addon through `V2Bridge`. It shows
capability requests, metadata, notifications and streams while the SDK owns
admission, callbacks, request waiters, timeouts and fragments.

## Build the addon

The default package includes every capability this server calls:

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

The lean `--base` package has only the two read-only snapshots and cannot serve
`!echo`, `!stream`, `!stream-cancel` or `!cmd`:

```bash
npm run build:base
npm run mcaddon:base
```

Import the generated `.mcaddon`. In the target world's settings enable
**Experiments → Beta APIs** and activate its behavior pack. Without Beta APIs,
scripts do not load and admission times out. See
[addon setup](../../addon/README.md#enable-in-a-world).

The default build bundles `addon/scripts/main.demo.ts` and calls
`registerDemoCapabilities()`; you do not copy files into the addon by hand.
`demoCapabilities.ts` is the shared registration source for `demo.echo`, the
`demo.ticks` stream and `run_world_command`, whose dangerous commands stay
denied.

## Run

From the repository root:

```bash
python examples/addon-server/server.py
python examples/addon-server/server.py --host 0.0.0.0 --port 8080 --log-level DEBUG
```

In game, run `/wsserver <python-host-ip>:8080`. Use a LAN IP for another device,
allow the listening port through the firewall, and stop Python with `Ctrl+C`.
Add `--log-raw` only when you deliberately want full packet payloads in logs.

## Input rule

Only messages whose trimmed text starts with ASCII `!` are handled. Empty or
whitespace input, ordinary chat, bare `help` / `帮助`, mid-sentence `!` and
full-width `！` are silent: no reply, no command, no capability or stream call.
A bare `!` returns one usage hint; an unknown `!command` returns one hint.
Prefixed Chinese aliases remain. SDK protocol frames and native events are not
filtered by this rule.

## Commands

| Chat command | Action | Package |
| --- | --- | --- |
| `!player [target]` / `!玩家` | `get_player_snapshot`, current player by default | Default + lean |
| `!inv [target]` / `!背包` | `get_inventory_snapshot` | Default + lean |
| `!wscmd <command>` / `!ws命令` | Native `command_client.run`, no addon capability needed | Default + lean |
| `!cmd <command>` / `!命令` | `run_world_command` as the chat sender, dangerous commands denied | Default only |
| `!echo <text>` | `demo.echo`, with `example.label` and `example.player` metadata | Default only |
| `!stream [count]` | Consume finite `demo.ticks` until normal completion (default 5) | Default only |
| `!stream-cancel [count]` | Consume a bounded prefix of an unbounded producer, then cancel (default 3) | Default only |
| `!help` / `!帮助` | Show command help | Default + lean |

With the lean `--base` package, the demo commands return a readable
`UNSUPPORTED_METHOD` error and a hint to use the default build.
`run_world_command` keeps the dangerous-command denylist; pass an explicit
allowlist to `handleRunWorldCommand` to narrow it further.

### Selectors and command context

Use `!cmd testfor @s` to target yourself or
`!cmd testfor @e[type=minecraft:cow]` to find cows in your dimension.
`!cmd` passes your exact player name, so `@s`, nearest-player selection and
relative coordinates use your execution context. If you leave before execution,
the request fails. `!wscmd` uses the WebSocket connection's native context.

Direct `run_world_command` calls may omit `player` to retain the overworld
dimension context. See [world command safety](../../addon/README.md#world-command-safety).
Zero `successCount` means zero successful applications and is displayed explicitly.

Entity types require a selector such as `@e[type=minecraft:cow]`; a bare `cow`
does not select by type. `setblock` requires three coordinates (for example,
`setblock ~ ~ ~ stone`) and remains denied by the addon policy.

## Ordering, failures and cancellation

`McbeServer` requires MCBEWS/2 admission. Same-player callbacks remain FIFO;
other players can progress independently. The reader continues receiving replies
while a hook awaits work. The example keeps no request wait table, manual
fragment state or background request tasks.

Negotiated requests return a response envelope; successful data is in `result`.
Remote protocol errors raise `RemoteCallError` with `code`. Native replies carry
`statusCode`. Timeouts, unsupported features and transport failures end the call
explicitly. Player-facing failures show the capability, error code, a bounded
message (at most 256 UTF-8 bytes) and an action hint; internal request IDs and
stack traces stay in logs.

Streams use `async with stream` so early exit cancels the producer. Cancellation
does not undo already executed world changes. Slow consumers terminate under
bounded buffers; receiver credits are not implemented.

Use `event.sender`, not the connection's deprecated last-player pointer. The
example filters external tellraw echoes.

This example has no authentication: use a trusted local/LAN environment and add
host authorization for production. Automated examples do not establish new
Minecraft platform or version support. See
[extension authors](../../docs/extension-authors.md) for registration, event
subscriptions and protocol limits.

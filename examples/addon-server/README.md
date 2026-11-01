# Addon capability server

A `McbeServer` host that calls the companion addon through `V2Bridge`. It shows
capability requests, metadata, notifications and streams while the SDK owns
admission, callbacks, request waiters, timeouts and fragments.

## Build the addon

Basic package, with the two read-only snapshots:

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

Complete demo package, required for `!echo`, `!stream`, `!stream-cancel` and
`!cmd`:

```bash
npm run build:demo
npm run mcaddon:demo
```

Import the generated `.mcaddon`. In the target world's settings enable
**Experiments → Beta APIs** and activate its behavior pack. Without Beta APIs,
scripts do not load and admission times out. See
[addon setup](../../addon/README.md#enable-in-a-world).

The demo build bundles `addon/scripts/main.demo.ts` and calls
`registerDemoCapabilities()`; you do not copy files into the addon by hand.
`demoCapabilities.ts` is the shared registration source for `demo.echo`, the
`demo.ticks` stream and the restricted `run_world_command` handler.

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
| `!player [target]` / `!玩家` | `get_player_snapshot`, current player by default | Basic |
| `!inv [target]` / `!背包` | `get_inventory_snapshot` | Basic |
| `!wscmd <command>` / `!ws命令` | Native `command_client.run`, no addon capability needed | Basic |
| `!cmd <command>` / `!命令` | `run_world_command`, restricted | Demo |
| `!echo <text>` | `demo.echo`, with `example.label` and `example.player` metadata | Demo |
| `!stream [count]` | Consume finite `demo.ticks` until normal completion (default 5) | Demo |
| `!stream-cancel [count]` | Consume a bounded prefix of an unbounded producer, then cancel (default 3) | Demo |
| `!help` / `!帮助` | Show command help | Basic |

With the basic package, demo commands return a readable `UNSUPPORTED_METHOD`
error and a hint to use the demo build. `run_world_command` remains restricted
even in the demo build: its default allowlist is `say`, and the dangerous-command
denylist still applies.

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

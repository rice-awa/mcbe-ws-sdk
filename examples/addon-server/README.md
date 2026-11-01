# Addon capability server

A `McbeServer` host that calls the companion addon through `V2Bridge`. It
demonstrates capability requests, metadata, notifications and streams, while the
SDK takes care of negotiation, callbacks, request waiters, timeouts and
fragments.

## Build the addon

The default package includes every capability this server calls:

```bash
cd addon
npm ci
npm run build
npm run mcaddon
```

The lean `--base` package has only the two read-only snapshots and cannot serve
`!echo`, `!stream`, `!stream-cancel` or `!cmd`:

```bash
npm run build:base
npm run mcaddon:base
```

Import the generated `.mcaddon`. In the target world's settings, enable
**Experiments → Beta APIs** and activate its behavior pack. Without Beta APIs the
scripts never load and negotiation times out. The
[addon setup](../../addon/README.md#enable-in-a-world) guide has the full steps.

The default build bundles `addon/scripts/main.demo.ts` and calls
`registerDemoCapabilities()`, so you do not need to copy any files into the
addon by hand. `demoCapabilities.ts` is the single place that registers
`demo.echo`, the `demo.ticks` stream and `run_world_command`, which applies the
command denylist.

## Run

From the repository root:

```bash
python examples/addon-server/server.py
python examples/addon-server/server.py --host 0.0.0.0 --port 8080 --log-level DEBUG
```

In game, run `/wsserver <python-host-ip>:8080`. From another device, use the
Python machine's LAN IP and allow the listening port through its firewall. Stop
Python with `Ctrl+C`. Add `--log-raw` only when you really want full packet
payloads in the logs.

## Input rule

The example handles a message only when its trimmed text starts with ASCII `!`.
Empty or whitespace input, ordinary chat, a bare `help` / `帮助`, a `!` in the
middle of a sentence and the full-width `！` are all ignored: the example sends
no reply and makes no command, capability or stream call. A bare `!` returns a
single usage hint, and so does an unknown `!command`. The Chinese aliases still
work when prefixed with `!`. SDK protocol frames and native events are not
affected by this rule.

## Commands

| Chat command | Action | Package |
| --- | --- | --- |
| `!player [target]` / `!玩家` | `get_player_snapshot`, current player by default | Default + lean |
| `!inv [target]` / `!背包` | `get_inventory_snapshot` | Default + lean |
| `!wscmd <command>` / `!ws命令` | Native `command_client.run`, no addon capability needed | Default + lean |
| `!cmd <command>` / `!命令` | `run_world_command` as the chat sender, commands on the denylist rejected | Default only |
| `!echo <text>` | `demo.echo`, with `example.label` and `example.player` metadata | Default only |
| `!stream [count]` | Consume finite `demo.ticks` until normal completion (default 5) | Default only |
| `!stream-cancel [count]` | Consume a bounded prefix of an unbounded producer, then cancel (default 3) | Default only |
| `!help` / `!帮助` | Show command help | Default + lean |

With the lean `--base` package, the demo commands return a readable
`UNSUPPORTED_METHOD` error along with a hint to use the default build.
`run_world_command` always applies the command denylist. To narrow it further,
pass an explicit allowlist to `handleRunWorldCommand`.

### Selectors and command context

Use `!cmd testfor @s` to target yourself, or
`!cmd testfor @e[type=minecraft:cow]` to find cows in your dimension.
`!cmd` passes your exact player name, so `@s`, nearest-player selection and
relative coordinates all use your execution context. If you leave the game
before the command runs, the request fails. `!wscmd` uses the WebSocket
connection's native context instead.

A direct `run_world_command` call may omit `player` to keep the overworld
dimension context; see [world command safety](../../addon/README.md#world-command-safety).
A `successCount` of zero means the command was applied zero times, and the
example displays that explicitly.

To select by entity type, write a selector such as `@e[type=minecraft:cow]`; a
bare `cow` does not filter by type. `setblock` needs three coordinates (for
example, `setblock ~ ~ ~ stone`) and is still denied by the addon policy.

## Ordering, failures and cancellation

`McbeServer` starts business processing only after protocol negotiation
finishes. Callbacks for the same player run in order, and other players can
progress independently. The reader keeps receiving replies while a hook waits
for a result, so the example needs no request wait table, no manual fragment
state and no background request tasks.

A negotiated request returns a response envelope, and the successful data is in
`result`. A remote protocol error raises `RemoteCallError` with its `code`, and
native replies carry `statusCode`. Timeouts, unsupported features and transport
failures all end the call with an explicit error. When a call fails, the player
sees the capability, the error code, a short message (at most 256 UTF-8 bytes)
and an action hint, while internal request IDs and stack traces stay in the logs.

Consume streams with `async with stream` so that leaving early cancels the
producer. Cancelling does not undo world changes that already ran. Slow
consumers are terminated once their buffers fill up; receiver credits are not
implemented.

Use `event.sender` rather than the connection's deprecated last-player pointer.
The example also filters out external tellraw echoes.

This example has no authentication, so run it in a trusted local or LAN
environment and add host authorization before using anything like it in
production. Running the automated examples does not establish support for new
Minecraft platforms or versions. See
[extension authors](../../docs/extension-authors.md) for registration, event
subscriptions and protocol limits.

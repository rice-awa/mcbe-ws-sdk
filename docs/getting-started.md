# Getting started

This guide walks you through connecting a Bedrock client to a Python host with
`McbeServer`, running the bundled examples and fixing the problems people most
often hit on a first run.

## Prerequisites

- Python 3.11+.
- A Bedrock world where `/wsserver` is available and commands are allowed.
- The companion TypeScript addon, built with the
  dependencies pinned in [`addon/package.json`](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/package.json):
  `@minecraft/server` 2.8.0, `@minecraft/server-ui` 2.1.0, and a
  `@minecraft/server-gametest` beta matched to the 1.26.30 baseline.
- The addon pack manifests target `min_engine_version` `[1, 26, 30]`.

Both examples need the companion addon to complete protocol negotiation,
including the basic example that uses only native WebSocket commands.

## Install

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"
```

If you have a built wheel, `pip install ./dist/0.3.0rc1/python/mcbe_ws_sdk-0.3.0rc1-py3-none-any.whl` is enough; the
runtime dependencies are resolved from its metadata.

## Build and import the addon

The default package registers the two read-only snapshots, `demo.echo`, the
`demo.ticks` stream, and `run_world_command`:

```bash
cd addon
npm ci
npm run build
npm run mcaddon
cd ..
```

The lean package registers only the two read-only snapshots:

```bash
npm --prefix addon run build:base
npm --prefix addon run mcaddon:base
```

Both variants write the same final script path, `dist/scripts/main.js`, but they
are separate builds. The default pack produces `mc-ws-sdk.mcaddon`; the lean pack
produces `mc-ws-sdk-base.mcaddon` under `addon/dist/packages/`, so one does not
overwrite the other. `--base` or `MCBE_BASE=1` selects `main.ts`; the default
entry is `main.demo.ts`.

### Enable the world

1. Import the generated `.mcaddon` into Minecraft.
2. Open the **target world** settings (create a world, or **Edit** an existing
   one).
3. Under **Experiments**, enable **Beta APIs**.
4. Activate the behavior pack (and the companion resource pack if prompted).
5. Enter the world. Without Beta APIs the scripts never load and negotiation
   times out.

Enabling experiments can make a world ineligible for some achievements. Use a
dedicated test world when possible.

## Start the example server

The basic example calls no addon capability. It answers commands that start
with `!` and waits for native `commandRequest` replies:

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

Use `--log-level DEBUG` for lifecycle diagnostics. Stop it with `Ctrl+C`.

Then in Minecraft run:

```text
/wsserver 127.0.0.1:8080
```

Use `127.0.0.1` when Python and the game run on the same machine. From another
device, use the Python machine's LAN IP and allow the listening port through
its firewall. There is no login step and no authentication in the examples.

A connection is ready only when protocol negotiation reaches `READY_V2`.
`on_connected` fires at that moment, and no business message is handled before
it.

## Commands in the examples

Both examples share one input rule: after trimming surrounding whitespace, the
message must start with ASCII `!`. Everything else is ignored: the example
sends no reply and runs no command, capability or stream call.

| Input | Result |
| --- | --- |
| empty or whitespace | silent |
| `hello`, `a`, `stream 5` | silent |
| bare `help` or `帮助` | silent |
| `hello !player`, full-width `！player` | silent |
| ` !player ` | handled after trimming |
| bare `!` | one usage hint |
| `!unknown` | one unknown-command hint |
| `!help`, `!帮助` | help |
| other `!command` | dispatched |

The filter applies only to the example's player-command dispatch. SDK protocol
frames, negotiation, `MCBEWS2|` carrier traffic, native `commandResponse` /
`error` frames and native events are never filtered by this rule.

Basic example commands:

| Command | Action |
| --- | --- |
| `!help` / `!帮助` | Show help |
| `!wscmd <command>` | Run a native command and await its `commandResponse` |

Addon example commands:

| Command | Action |
| --- | --- |
| `!player [target]` / `!玩家` | `get_player_snapshot`; current player by default |
| `!inv [target]` / `!背包` | `get_inventory_snapshot` |
| `!wscmd <command>` / `!ws命令` | Native command, no addon capability needed |
| `!cmd <command>` / `!命令` | `run_world_command` as the chat sender; commands on the denylist are rejected |
| `!echo <text>` | `demo.echo`, with example metadata |
| `!stream [count]` | Consume `demo.ticks` until normal completion (default 5) |
| `!stream-cancel [count]` | Consume a bounded prefix of an unbounded producer, then cancel (default 3) |
| `!help` / `!帮助` | Show help |

`run_world_command` ships in the default package. Its default policy is
denylist-only: every entrypoint on the command denylist is rejected.
Pass an explicit allowlist to `handleRunWorldCommand` to narrow it further.
`!cmd testfor @s` uses the sender's player context, including position and
dimension. Direct capability calls may pass an exact online `player` name;
omitting it retains the overworld context. A zero `successCount` is shown as
zero successful applications.

## Call an addon capability from Python

From `on_player_message` or another managed hook on a ready connection, use
the server's `V2Bridge`:

```python
response = await server.v2.request(
    state.id,
    "get_player_snapshot",
    {"target": event.sender},
)
print(response["result"])
```

The SDK matches replies to requests and handles timeouts, fragmentation and
reassembly. When the remote side returns a protocol error, the call raises
`RemoteCallError` with the wire `code`. For native commands, also check
`statusCode`, because receiving a response does not mean the command succeeded.

To consume a stream:

```python
stream = await server.v2.stream(state.id, "demo.ticks", {"count": 3})
async with stream:
    async for item in stream:
        print(item)
```

Streams require `stream/1` in the negotiated feature set. The default package
registers the `demo.ticks` producer. Use `async with` or `await stream.aclose()`
when consumption may stop early; cancelling a pending read cancels that stream,
not other requests.

## Errors players see

When an application-level call fails, show the player:

- the method or capability being called,
- a stable error code,
- a short readable message (at most 256 UTF-8 bytes, never cutting a character
  in half),
- an action hint, such as “use the default package” or “check the parameter”.

Keep internal request IDs, trace IDs and stack traces in the logs only. The addon maps
unknown capabilities, invalid parameters and handler failures to
`UNSUPPORTED_METHOD`, `INVALID_PARAMS` and `HANDLER_FAILED`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Connection closes immediately with `UNSUPPORTED_PROTOCOL` | The addon or client is not speaking MCBEWS/2. Old protocol frames are rejected; there is no fallback. |
| `NEGOTIATION_TIMEOUT` | The addon is absent, not active, or scripts are not loaded. Confirm the pack and **Beta APIs**. |
| `NEGOTIATION_CONFIRMATION_TIMEOUT` | The addon answered the challenge but the final confirmation did not complete. Reconnect and check the world/log for script errors. |
| `NEGOTIATION_BUSY` | Too many negotiations or peers are in progress at once; reconnect after the previous session expires. |
| `INVALID_NEGOTIATION` | A malformed or mismatched control frame. Verify the addon version matches this SDK. |
| `SESSION_INVALID` | The active session was revoked or became stale. Reconnect; the new session gets a fresh epoch. |
| Capability call times out | Confirm the active pack, the target world, Beta APIs and the live `/wsserver` connection. |
| `!cmd` fails while `!wscmd` works | The command is on the command denylist, or the pack is the lean `--base` build. |
| Ordinary chat gets no reply | This is expected. Examples answer only trimmed messages that start with ASCII `!`. |
| Text arrives in pieces | The SDK splits tellraw and scriptevent text to fit a 461-byte empirical budget. Native commands are never split and fail if they exceed it. |

When negotiation fails, the WebSocket closes with code `1008` and a short reason
token of at most 123 UTF-8 bytes. See [diagnostics](diagnostics.md) for
connection and transport errors.

## Shutdown and rollback

To shut down, call `await server.stop()` or cancel `run_lifetime()`. The SDK
cleans up managed tasks, pending calls and fragments. Reconnecting starts a new
negotiation and a fresh session. World changes that already ran are not undone
and are never replayed automatically.

The examples have no authentication, and the bridge is not a security boundary.
Your host is responsible for authenticating and authorizing callers and for
application state.

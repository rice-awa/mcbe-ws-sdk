# Basic WebSocket server

A minimal `McbeServer` host. It demonstrates the connection lifecycle, managed
hooks, `!` command dispatch, tellraw replies and native `commandRequest`
correlation. The SDK takes care of negotiation, request waiters, timeouts and
fragments, and the example only supplies the chat behavior.

## Run

From the repository root:

```bash
pip install -e ".[dev]"
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

In game, run `/wsserver <python-host-ip>:8080`. Use `127.0.0.1` when Python and
the game are on the same machine. From another device, use the Python machine's
LAN IP and allow the port through its firewall. Pass `--log-level DEBUG` to see
lifecycle diagnostics, and press `Ctrl+C` to stop the server.

## Input rule

The example handles a message only when its trimmed text starts with ASCII `!`.
Empty or whitespace input, ordinary chat, a bare `help` / `帮助`, a `!` in the
middle of a sentence and the full-width `！` are all ignored: the example sends
no reply and makes no command or capability call. A bare `!` returns a single
usage hint, and so does an unknown `!command`. The rule applies to the example
only; it never filters SDK protocol frames or native events.

## Commands

| Command | Action |
| --- | --- |
| `!help` / `!帮助` | Show help |
| `!wscmd <command>` | Await a native command response, for example `!wscmd time query daytime` |

Install the companion addon and enable **Experiments → Beta APIs** in the
world. Even though this example calls no addon capability, the addon is still
what completes protocol negotiation. `!wscmd` uses the SDK's native command
client and returns the typed `MinecraftCommandResponse`, so check `statusCode`.
Without the addon, the server logs `NEGOTIATION_TIMEOUT` and closes the
connection. It never falls back to handling chat.

## Behavior

- `on_connected` fires only after MCBEWS/2 negotiation reaches `READY_V2`.
- Callbacks for the same player run in order, while different players can
  progress concurrently. The reader keeps receiving replies while a hook waits
  for a command.
- The example filters external tellraw echoes with `is_external_echo()`, so it
  never answers its own messages.
- Cancelling ends the wait, but it cannot undo a command that has already run.

For inventory, capabilities, metadata and streams, see the
[addon example](../addon-server/README.md) and
[extension authors](../../docs/extension-authors.md). Worlds that use the addon
must have **Experiments → Beta APIs** enabled.

This example has no authentication, so run it on a trusted local or LAN network
and add host authorization before using it in production. Running the automated
examples does not establish support for new Minecraft platforms or versions.

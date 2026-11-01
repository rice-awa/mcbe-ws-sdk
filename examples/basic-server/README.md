# Basic WebSocket server

A minimal `McbeServer` host. It shows the connection lifecycle, managed hooks,
`!` command dispatch, tellraw replies and native `commandRequest` correlation.
The SDK owns admission, request waiters, timeouts and fragments; the example
owns the chat behavior.

## Run

From the repository root:

```bash
pip install -e ".[dev]"
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

In game, run `/wsserver <python-host-ip>:8080`. Use `127.0.0.1` on the same
machine, a LAN IP from another device, and allow the port through the firewall.
Use `--log-level DEBUG` for lifecycle diagnostics and `Ctrl+C` to stop.

## Input rule

Only messages whose trimmed text starts with ASCII `!` are handled. Empty or
whitespace input, ordinary chat, bare `help` / `帮助`, mid-sentence `!` and
full-width `！` are silent: no reply, no command, no capability call. A bare `!`
returns one usage hint; an unknown `!command` returns one hint. The rule applies
to the example only; SDK protocol and native events are never filtered by it.

## Commands

| Command | Action |
| --- | --- |
| `!help` / `!帮助` | Show help |
| `!wscmd <command>` | Await a native command response, for example `!wscmd time query daytime` |

Admission is mandatory, so the MCBEWS/2 addon must be installed in the world
even though this example calls no addon capability. `!wscmd` uses the SDK's
native command client and returns the typed `MinecraftCommandResponse`; inspect
`statusCode`. Without the addon the server logs `NEGOTIATION_TIMEOUT` and
closes the connection — it never falls back to processing chat.

## Behavior

- `on_connected` fires only after MCBEWS/2 admission reaches `READY_V2`.
- Same-player callbacks stay ordered; different players may progress
  concurrently. The reader keeps receiving replies while a hook awaits a
  command.
- The example filters external tellraw echoes with `is_external_echo()` so it
  does not answer itself.
- Cancellation ends the wait but cannot undo a command already executed.

For inventory, capabilities, metadata and streams, see the
[addon example](../addon-server/README.md) and
[extension authors](../../docs/extension-authors.md). Addon worlds must enable
**Experiments → Beta APIs**.

This example has no authentication; run it on a trusted local/LAN network and
add host authorization for production. Automated examples do not establish new
Minecraft platform or version support.

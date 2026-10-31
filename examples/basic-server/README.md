# Basic WebSocket server

This example uses `McbeServer` from the unreleased checkout; PyPI `0.2.1` does
not contain this entry point. The SDK manages reliable dispatch, command
waiters and fragments. The example prints chat, replies with tellraw and
filters external-message echoes.

## Run

From the repository root:

```bash
pip install -e ".[dev]"
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

In game: `/wsserver <python-host-ip>:8080`, then send chat. Use `127.0.0.1` on
the same machine, a LAN IP from another device, and allow the port through the
firewall. `!wscmd time query daytime` awaits a native command response without
an addon. Use `--log-level DEBUG` for diagnostics and `Ctrl+C` to stop.

Same-player callbacks remain ordered; different players may progress
concurrently. The reader continues receiving replies while hooks await commands.
Commands return `MinecraftCommandResponse`; inspect `statusCode`. The SDK owns
timeouts, disconnect and cancellation without host wait tables or request tasks.
Cancellation cannot undo executed commands. The legacy entry keeps its ordering;
see [migration](../../docs/migration.md).

This example needs no addon. For inventory, custom capabilities, metadata,
events and streams, see the [addon example](../addon-server/README.md) and
[extension authors](../../docs/extension-authors.md). Addon worlds must enable
**Experiments → Beta APIs**. This example has no authentication; run on a trusted
local/LAN network. Live-game acceptance and the full compatibility matrix remain
pending; no platform/version support is expanded.

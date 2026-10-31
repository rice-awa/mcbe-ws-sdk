# Getting started

This guide uses `McbeServer` from the **unreleased checkout**. The current PyPI
release is `0.2.1`; `pip install mcbe-ws-sdk` does not install these new APIs.
For the old entry point and callback contract, see [migration](migration.md).

## Install this checkout

Requires Python 3.11+ and a Bedrock client/world where `/wsserver` is available
and commands are permitted. No additional platform or game-version support is
claimed by the new APIs; live-game acceptance remains pending.

```bash
cd mcbe-ws-sdk
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"
```

## Connect and reply

Start the runnable echo server:

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

In the game, run `/wsserver 127.0.0.1:8080` when Python runs on the same
machine. From another device, replace `127.0.0.1` with the Python machine's
LAN IP and allow the listening port through its firewall. Send a chat message;
the example sends a tellraw reply and filters the game's external-message echo.
Use `--log-level DEBUG` for diagnostics and `Ctrl+C` to stop.

For a minimal host that prints chat and executes a command:

```python
import asyncio
from mcbe_ws_sdk import McbeServer, NoOpHook

class Hook(NoOpHook):
    async def on_connected(self, state):
        print("connected:", state.id)

    async def on_player_message(self, state, event, parsed=None):
        if event.sender in {"External", "外部"}:
            return
        print(event.sender, event.message)
        if event.message == "!daytime":
            response = await state.command_client.run("time query daytime")
            print(response)

async def main():
    server = McbeServer(hook=Hook())
    await server.run_lifetime()

asyncio.run(main())
```

`McbeServer` uses SDK-managed tasks. You can await a reply in a hook without
blocking the WebSocket reader. Messages from one player remain FIFO; different
players may progress concurrently. Synchronous hooks must stay nonblocking.
No application-owned wait table, request task or fragment buffer is needed.
`McbeServer()` keeps a neutral `DefaultResponseSink`: it does not automatically
render your application's replies. The basic example supplies a Minecraft sink
using `McbeOutboundDelivery` and queues `OutboundText` for delivery.

Use `event.sender` as the current player identity and reply target. A single
connection may carry many players; bucket application history and locks by
`(state.id, event.sender)`. The deprecated `state.player_name` is only a
last-speaker convenience pointer.

## Query inventory through the addon

Build the companion addon:

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

Import the generated `.mcaddon`, enable **Experiments → Beta APIs** in the
**target world's** settings, and activate its behavior pack. Without Beta APIs,
scripts do not load and capability requests time out. See the
[addon setup](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md#enable-in-a-world).
Then start the example from the repository root:

```bash
python examples/addon-server/server.py
```

Connect with `/wsserver <host-ip>:8080`, then send `!inv` or `!player`.
`!wscmd time query daytime` uses native commands and needs no addon.
`!cmd` uses a separately enabled addon world-command handler.

Inside your own managed hook, use the same server's negotiated bridge:

```python
response = await server.v2.request(
    state.id, "get_inventory_snapshot", {"target": event.sender}
)
print(response["result"])
```

The SDK owns correlation, timeouts, chunking and reassembly. Remote v2 errors
raise `RemoteCallError`; v1 fallback retains its capability-level result inside
`result`, including any `ok: false`. Inspect failures rather than treating a
received response as proof of success. Native command responses also need their
`statusCode` checked. Cancelling the caller ends its wait; it cannot undo an
already executed command.

## Extend the host

| Need | Public entry |
| --- | --- |
| Custom Python capability | `server.v2.registry.register(name, handler, validate=...)` |
| Custom addon capability | `registerCapability(name, handler)` in the addon router |
| Metadata and trace | `server.v2.request(..., metadata=..., trace_id=...)` |
| Event observer | `await state.native_events.subscribe(name, observer)` |
| Python stream producer | `server.v2.streams.register(name, handler)` |
| Stream consumer | `await server.v2.stream(state.id, name, params)` |

See [extension authors](extension-authors.md) for complete registration and
consumption snippets, cancellation and limits. The addon example includes
optional custom capability and stream registrations; follow its README before
using `!echo`, `!stream` or `!stream-cancel`.

Discovery is bounded and independent of chat/native commands. Old addons may
fall back to known v1 methods. Metadata and streams require negotiated features
and fail explicitly when unavailable. No required fields are silently lost.
Streams have bounded buffers and terminate on slow consumers; receiver credits
are not implemented. See [v2](protocol-v2.md).

## Existing hosts and troubleshooting

`McbeServerFacade()` still defaults to the old serial callback path. Only that
path needs a separately managed background task when a hook waits for a reply
that the reader must receive. Do not copy that workaround into the new entry
point. Compare defaults and return values in the [migration table](migration.md).

- Connection fails: check the listening process, IP, port and firewall.
- Addon call times out: check the active pack, **Experiments → Beta APIs**, and
  the live `/wsserver` connection.
- Text arrives in pieces: the SDK enforces a default 461-byte command budget;
  deployments may lower it. Native commands remain atomic and reject overflow.
- Shutdown: `await server.stop()` or cancel `run_lifetime()`; managed work and
  pending calls are cleaned up. Reconnect never replays world mutations.

The examples have no authentication. The bridge is not a security boundary;
your host owns caller authentication, authorization and application state.

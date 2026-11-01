# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](./LICENSE)

**Bring your Python ideas into Minecraft Bedrock Edition.**

`mcbe-ws-sdk` is a general-purpose WebSocket gateway SDK for Minecraft Bedrock.
Use familiar async Python to respond to chat commands, inspect player inventories,
connect addon capabilities to your services or consume data as a stream.

The project includes a Python SDK and a companion TypeScript addon. Together they
manage connections, protocol negotiation, request correlation, fragmentation and
cleanup when a connection ends. Build a chat bot, a game utility or your own
interactive experience, and connect it to the model services, message systems
and application state you already use. Your host controls authentication and
authorization.

**The current protocol is V2 (MCBEWS/2)**, with bidirectional capability calls,
notifications, metadata and request streams. SDK, addon and protocol versions
are managed separately; see [migration](docs/migration.md) for the version axes.

[Read the docs](https://rice-awa.github.io/mcbe-ws-sdk/) · [Get started](docs/getting-started.md) · [Explore the example](examples/addon-server/README.md)

```text
Minecraft world (companion addon) ←── /wsserver ──→ Python app (McbeServer)
                                                      ├── ConnectionHook: input
                                                      ├── ResponseSink: replies
                                                      └── server.v2: capabilities and streams
```

## What you can build

- **Respond to game actions with async Python.** Handle player messages in a
  `ConnectionHook`, run native commands and await addon results while the SDK
  reader continues processing replies.
- **Connect Python and the addon in both directions.** Register capabilities on
  either side, exchange requests and notifications, and carry call context in
  metadata. New game-side features use the same request mechanism.
- **Receive data as it is produced.** Request streams support normal completion
  and cancellation. Large items are fragmented and reassembled before the
  consumer receives each complete logical item.
- **Keep player interactions organized.** Messages retain their sender identity;
  work from one player stays ordered while other players can progress
  independently. Store application context by connection and sender.
- **Create your own in-game interaction.** Render results through a `ResponseSink`
  and `McbeOutboundDelivery`, or use native event subscriptions and interceptors
  to extend how your host receives input.
- **Start with a working example.** The default addon includes player and
  inventory snapshots, echo, streams and world commands. Run the demo first,
  then replace its handlers with your own application logic.

## Run your first example

You need Python 3.11+, Node.js 22 and a Bedrock world with `/wsserver` available.
Both examples need the companion addon for protocol negotiation.

From the repository root, install the SDK and build the addon:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"
npm ci --prefix addon
npm --prefix addon run mcaddon:production
```

Import `addon/dist/packages/mc-ws-sdk.mcaddon` into Minecraft. Enable
**Experiments → Beta APIs** in the target world and activate the behavior pack.
Without Beta APIs, scripts do not load and protocol negotiation times out.
The addon targets engine 1.26.30 with server 2.8.0 / server-ui 2.1.0;
see the [addon README](addon/README.md) for the full dependency list.

Start the server:

```bash
python examples/addon-server/server.py --host 0.0.0.0 --port 8080
```

Connect from Minecraft and query your inventory:

```text
/wsserver <python-host-ip>:8080
!inv
```

Use `127.0.0.1` on the same machine or the host's LAN IP from another device.
Send `!help` for commands, `!echo hello` for an echo or `!stream 5` for five items
followed by completion. The example handles only messages starting with ASCII
`!`; ordinary chat stays silent. Stop the server with `Ctrl+C`.

## Integrate with your application

Start with a hook when integrating the SDK into your own application. This
host listens for `!inv`, queries the sender's inventory and prints the result.
The SDK handles request IDs, sending, timeouts and reply correlation:

```python
import asyncio

from mcbe_ws_sdk import McbeServer, NoOpHook


class InventoryHook(NoOpHook):
    async def on_player_message(self, state, event, parsed=None):
        if event.message.strip() != "!inv":
            return
        response = await server.v2.request(
            state.id, "get_inventory_snapshot", {"target": event.sender}
        )
        print(response["result"])


server = McbeServer(hook=InventoryHook())
asyncio.run(server.run_lifetime())
```

To display results in the game, add a `ResponseSink` backed by
`McbeOutboundDelivery`; the [runnable example](examples/addon-server/server.py)
shows the complete setup. For multiplayer applications, store player state by
`(connection_id, event.sender)` so each player has a separate business context.

## Find what you need

| Goal | Guide |
| --- | --- |
| Install, connect, run commands and troubleshoot | [Getting started](docs/getting-started.md) |
| Add capabilities, notifications, metadata and streams | [Extension authors](docs/extension-authors.md) |
| Understand queues, lifetimes and ownership | [Architecture](docs/architecture.md) |
| Subscribe to or intercept game events | [Native events](docs/native-events.md) |
| Trace requests and diagnose errors | [Diagnostics](docs/diagnostics.md) |
| Inspect message fields and transport limits | [V2 protocol](docs/protocol-v2.md) |
| Upgrade from 0.2.x or roll back a deployment | [Migration](docs/migration.md) |
| Look up classes, methods and parameters | [API reference](docs/reference.md) |

The default addon includes snapshots, echo, streams and world commands. For
snapshots alone, build the lean pack with `npm --prefix addon run mcaddon:base`.
World commands apply a [command denylist](addon/README.md#world-command-safety);
the host authorizes callers. Native commands stay atomic and reject overflow;
tellraw and script events can be fragmented within the default 461-byte budget.

## Development and checks

```bash
ruff check --no-cache src tests examples tools
mypy --no-incremental src
MCBE_REQUIRE_CROSS_LANGUAGE=1 pytest -p no:cacheprovider -q
mkdocs build --strict
python tools/format.py --check
```

Cross-language tests use Node.js 22 and the addon's TypeScript dependencies;
run `npm ci --prefix addon` first. Plain pytest skips runner-dependent cases
when tools are missing; `MCBE_REQUIRE_CROSS_LANGUAGE=1` makes missing tools fail.

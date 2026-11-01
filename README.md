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

The SDK and the addon talk over **V2 (MCBEWS/2)**, which supports capability
calls in both directions, notifications, metadata and request streams. The SDK,
the addon and the protocol are versioned independently; [migration](docs/migration.md#version-numbers)
lists the current values.

[Read the docs](https://rice-awa.github.io/mcbe-ws-sdk/) · [Get started](docs/getting-started.md) · [Explore the example](examples/addon-server/README.md)

```text
Minecraft world (companion addon) ←── /wsserver ──→ Python app (McbeServer)
                                                      ├── ConnectionHook: input
                                                      ├── ResponseSink: replies
                                                      └── server.v2: capabilities and streams
```

## What you can build

- **Respond to game actions with async Python.** Handle player messages in a
  `ConnectionHook`, run native commands and await addon results. While your
  handler waits, the SDK keeps reading and matches each reply to its request.
- **Call both ways between Python and the addon.** Either side can register
  capabilities, send requests and notifications, and attach call context as
  metadata. New game-side features reuse the same request mechanism.
- **Receive data as it is produced.** A request stream can finish normally or be
  cancelled. Large items are fragmented for transport and reassembled, so your
  code always receives complete items.
- **Keep each player's interactions separate.** Every message carries its sender.
  Work from one player runs in order, while different players can progress
  independently, so you can store application context by connection and sender.
- **Shape the in-game experience.** Render results with a `ResponseSink` and
  `McbeOutboundDelivery`, or use native event subscriptions and interceptors to
  change how your host receives input.
- **Start from a working example.** The default addon already provides player
  and inventory snapshots, echo, streams and world commands. Run the demo first,
  then swap in your own handlers.

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
Without Beta APIs the scripts never load, and protocol negotiation times out.
The addon targets Minecraft engine 1.26.30; the full requirements are in the
[addon README](addon/README.md#requirements).

Start the server:

```bash
python examples/addon-server/server.py --host 0.0.0.0 --port 8080
```

Connect from Minecraft and query your inventory:

```text
/wsserver <python-host-ip>:8080
!inv
```

Use `127.0.0.1` when Python and the game run on the same machine, and the
LAN IP of the Python machine when connecting from another device. Then try:

- `!help` to list the commands.
- `!echo hello` to get an echo back.
- `!stream 5` to receive five items followed by completion.

The example reacts only to messages that start with ASCII `!`, so ordinary chat
stays silent. Press `Ctrl+C` to stop the server.

## Integrate with your application

To use the SDK in your own application, start with a hook. The host below
listens for `!inv`, asks the addon for the sender's inventory and prints the
result. The SDK takes care of request IDs, sending, timeouts and matching the
reply to its request:

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

To show results in the game, add a `ResponseSink` backed by
`McbeOutboundDelivery`; the [runnable example](examples/addon-server/server.py)
has the complete setup. In a multiplayer world, key your per-player state by
`(connection_id, event.sender)` so each player keeps a separate context.

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

## Good to know

- **Choosing an addon pack.** The default addon includes snapshots, echo,
  streams and world commands. If you only need snapshots, build the lean pack
  with `npm --prefix addon run mcaddon:base`.
- **World commands.** They pass through a
  [command denylist](addon/README.md#world-command-safety), but deciding who may
  call them is still up to your host.
- **Long text.** Native commands are never split and fail if they exceed the
  byte budget. tellraw and script events can be split to stay within the default
  461-byte budget.

## Development and checks

To work on the SDK itself, run these checks from the repository root:

```bash
ruff check --no-cache src tests examples tools
mypy --no-incremental src
MCBE_REQUIRE_CROSS_LANGUAGE=1 pytest -p no:cacheprovider -q
mkdocs build --strict
python tools/format.py --check
```

Cross-language tests need Node.js 22 and the addon's TypeScript dependencies, so
run `npm ci --prefix addon` first. When those tools are missing, plain pytest
skips the tests that depend on them; with `MCBE_REQUIRE_CROSS_LANGUAGE=1` a
missing tool fails the run instead.

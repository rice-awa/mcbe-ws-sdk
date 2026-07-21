# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](./LICENSE)

Generic **WebSocket gateway SDK** for Minecraft Bedrock Edition.

It owns the WS transport, packet protocol, and byte-safe command chunking
(461-byte hard limit). Your host injects behaviour through `ConnectionHook` and
`ResponseSink`, and drives the stack with `McbeServerFacade`.

There is **no** message broker and **no** LLM worker inside the SDK — those stay
in the host application.

```text
Minecraft client  ←── /wsserver IP:port ──→  Your Python host (this SDK)
```

## Install

```bash
pip install mcbe-ws-sdk
```

Editable install for development:

```bash
pip install -e ".[dev,docs]"
```

Requires **Python 3.11+**.

## 30-second taste

```python
import asyncio
from mcbe_ws_sdk import McbeServerFacade, NoOpHook


class MyHook(NoOpHook):
    async def on_connected(self, state):
        print("connected:", state.id)

    async def on_player_message(self, state, event, parsed=None):
        print(f"{event.sender}: {event.message}")


async def main() -> None:
    facade = McbeServerFacade(hook=MyHook())
    print(f"ws://{facade.settings.websocket.host}:{facade.settings.websocket.port}")
    await facade.run_lifetime()


if __name__ == "__main__":
    asyncio.run(main())
```

Then in Minecraft: `/wsserver <this-machine-ip>:8080`

For a **reply-with-tellraw** host, run the ready-made example:

```bash
python examples/basic-server/server.py
```

## Documentation

Full beginner tutorial, architecture, protocol, and API reference live on the
docs site (English + 中文):

| | |
|---|---|
| **Online** | https://rice-awa.github.io/mcbe-ws-sdk/ |
| **Local** | `pip install -e ".[docs]" && mkdocs serve` → http://127.0.0.1:8000 |

| Page | Content |
|------|---------|
| [Getting Started](./docs/getting-started.md) | Install, 5-minute walkthrough, minimal echo bot, FAQ |
| [Architecture](./docs/architecture.md) | Layer stack and dependency inversion |
| [Protocol](./docs/addon-bridge-protocol.md) | mcbews v1 bridge wire format |
| [API Reference](./docs/reference.md) | Generated from source |

## Examples

| Path | What it shows |
|------|---------------|
| [`examples/basic-server/`](./examples/basic-server/) | Echo chat with `tellraw` (start here) |
| [`examples/addon-server/`](./examples/addon-server/) | Capability calls via the companion addon |
| [`examples/addon-capability-call/`](./examples/addon-capability-call/) | In-memory bridge round-trip (no game) |

Companion TypeScript addon: [`addon/`](./addon/). Worlds that load it must enable
**Experiments → Beta APIs** (see [addon README](./addon/README.md#enable-in-a-world)).

## Development

```bash
pip install -e ".[dev,docs]"
ruff check --no-cache src tests examples
mypy --no-incremental src
pytest -p no:cacheprovider -q
python tools/format.py          # ruff format+fix (Python); prettier (Addon if Node)
python tools/format.py --check  # CI-style check
```

## License

[MIT](./LICENSE)

# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](./LICENSE)

Generic **WebSocket gateway SDK** for Minecraft Bedrock Edition.

It owns WebSocket transport, request correlation, the addon bridge and byte-safe
command chunking. New hosts use `McbeServer` and await commands and capabilities
inside managed hooks. `McbeServerFacade` retains its serial default behavior.
Model services, message brokers, authentication and business history stay in the host.

> **Version scope:** the current PyPI release is `0.2.1`. The new entry point,
> v2, metadata and continuous streams described here belong to the unreleased
> checkout. Use the editable install below; installing PyPI `0.2.1` does not
> provide these APIs.

```text
Minecraft client  ←── /wsserver IP:port ──→  Python host
```

## Addon and bidirectional capabilities

The new server negotiates v2 for bidirectional capabilities, notifications,
metadata and continuous streams. Old addons fall back only to known v1 methods;
required metadata and streams never silently downgrade. The SDK owns waiters
and fragments; extension authors register handlers.

The target world must enable **Experiments → Beta APIs** to load the addon.
The bridge is not a security boundary; hosts own authentication and authorization.
New APIs have automated coverage; live-game acceptance and the full compatibility
matrix remain pending. No new platform or game-version support is claimed.

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

Install this checkout before running this code.

```python
import asyncio
from mcbe_ws_sdk import McbeServer, NoOpHook


class MyHook(NoOpHook):
    async def on_connected(self, state):
        print("connected:", state.id)

    async def on_player_message(self, state, event, parsed=None):
        print(f"{event.sender}: {event.message}")


async def main() -> None:
    server = McbeServer(hook=MyHook())
    print(f"ws://{server.settings.websocket.host}:{server.settings.websocket.port}")
    await server.run_lifetime()


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
| [Migration](./docs/migration.md) | Entry points, ordering, returns, fallback and cancellation |
| [Extension authors](./docs/extension-authors.md) | Custom capabilities, metadata, events and streams |
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

The v2 Python/TypeScript cross-language tests require Node.js 22 and
`npm ci --prefix addon`. Without Node.js or TypeScript, only tests that call the
TypeScript runner skip; Python-only tests still run. To require cross-language
checks locally (as the mandatory `cross-language` CI job does):

```bash
MCBE_REQUIRE_CROSS_LANGUAGE=1 python -m pytest -p no:cacheprovider -q \
  tests/unit/test_v2_codec.py tests/unit/test_v2_transport.py \
  tests/unit/test_v2_contract.py tests/unit/test_v2_tooling.py
```

Required mode fails if either tool is missing. Runner or protocol failures
always fail, including in ordinary development runs.

## License

[MIT](./LICENSE)

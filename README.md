# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](./LICENSE)

A generic **WebSocket gateway SDK** for Minecraft Bedrock Edition.

The SDK owns the WebSocket transport, MCBEWS/2 admission, request correlation,
byte-safe command chunking, capabilities and request streams. Your host keeps
model services, message brokers, authentication and business history.

```text
Minecraft client  ←── /wsserver IP:port ──→  Python host (McbeServer)
                                              │
                                              ├── ConnectionHook / ResponseSink
                                              └── V2Bridge ←→ companion addon
```

## What it provides

- One public Python entry point, `McbeServer`; every connection must finish
  MCBEWS/2 admission before business traffic runs.
- Bidirectional capabilities: Python calls the addon, the addon calls Python.
- Notifications, metadata and continuous request streams.
- Byte-safe tellraw/scriptevent delivery with a 461-byte empirical command
  budget that deployments may lower.
- A TypeScript companion addon that implements the Minecraft side of the wire
  protocol.

## Requirements

- Python 3.11+.
- Minecraft Bedrock with `/wsserver` available.
- For addon capabilities: the companion addon, and **Experiments → Beta APIs**
  enabled in the target world. Without Beta APIs the scripts never load and
  admission times out.

## Install

From a checkout of this repository:

```bash
pip install -e ".[dev,docs]"
```

If you have a built wheel or sdist, install that file directly:

```bash
pip install ./dist/mcbe_ws_sdk-*.whl
```

## First connection

Start the bundled command demo, which replies with tellraw:

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

In Minecraft, run `/wsserver <host-ip>:8080`, then send a prefixed command:

```text
!help
!wscmd time query daytime
```

The example handles only messages whose trimmed text starts with ASCII `!`.
Ordinary chat, bare `help` / `帮助`, mid-sentence `!` and full-width `！` are
ignored with no reply and no capability call. A bare `!` returns one usage
hint; an unknown `!command` returns one hint.

Objects that need the addon can call `server.v2.request(...)` from a managed
hook:

```python
import asyncio

from mcbe_ws_sdk import McbeServer, NoOpHook


class Hook(NoOpHook):
    def __init__(self) -> None:
        self.server: McbeServer | None = None

    async def on_player_message(self, state, event, parsed=None) -> None:
        message = event.message.strip()
        if message != "!inv":
            return
        response = await self.server.v2.request(
            state.id, "get_inventory_snapshot", {"target": event.sender}
        )
        print(response["result"])


async def main() -> None:
    hook = Hook()
    hook.server = McbeServer(hook=hook)
    await hook.server.run_lifetime()


asyncio.run(main())
```

`McbeServer()` uses a neutral response sink, so it prints but does not render
replies. Use `McbeOutboundDelivery` when you want to send tellraw, as the
example server does.

## Addon

The default package includes the two read-only snapshots, `demo.echo`, the
`demo.ticks` stream and `run_world_command`:

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

The lean package keeps only the two read-only snapshots:

```bash
npm run build:base
npm run mcaddon:base
```

Import the generated `.mcaddon`, enable **Experiments → Beta APIs** in the
target world, and activate the behavior pack. See
[addon/README.md](./addon/README.md) for dependencies, build variants and the
trust boundary.

## Documentation

| Page | Content |
| --- | --- |
| [Getting Started](./docs/getting-started.md) | Install, addon, first server, commands, troubleshooting |
| [Architecture](./docs/architecture.md) | Admission state machine, runtime, bridge layers |
| [Extension Authors](./docs/extension-authors.md) | Capabilities, notifications, metadata, streams |
| [Protocol (MCBEWS/2)](./docs/protocol-v2.md) | Admission, carrier, limits, streams |
| [Diagnostics](./docs/diagnostics.md) | Lifecycle logs, `--log-raw`, redaction |
| [Native Events](./docs/native-events.md) | Observers, interceptors, subscriptions |
| [Migration](./docs/migration.md) | Breaking changes from the dual-protocol release |
| [API Reference](./docs/reference.md) | Generated from the public source |

Chinese documentation is available under `/zh/` on the docs site and in the
`*.zh.md` files.

## Examples

| Path | What it shows |
| --- | --- |
| [`examples/basic-server/`](./examples/basic-server/) | `!` commands and native `commandRequest` replies |
| [`examples/addon-server/`](./examples/addon-server/) | Addon capabilities, metadata and streams |

## Development

```bash
pip install -e ".[dev,docs]"
ruff check --no-cache src tests examples
mypy --no-incremental src
pytest -p no:cacheprovider -q
python tools/format.py          # ruff format+fix; prettier for the addon
python tools/format.py --check  # CI-style check
```

MCBEWS/2 cross-language tests require Node.js 22 and `npm ci --prefix addon`.
Without Node.js or TypeScript only the runner-dependent tests skip; protocol or
runner failures always fail. To require both tools:

```bash
MCBE_REQUIRE_CROSS_LANGUAGE=1 python -m pytest -p no:cacheprovider -q \
  tests/unit/test_v2_codec.py tests/unit/test_v2_transport.py \
  tests/unit/test_v2_contract.py tests/unit/test_v2_tooling.py
```

## License

[MIT](./LICENSE)

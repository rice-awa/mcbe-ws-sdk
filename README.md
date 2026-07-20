# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)

Generic WebSocket gateway SDK for Minecraft Bedrock Edition. The package owns the
WS transport, packet protocol and byte-safe command chunking, and exposes a
dual-layer interface the host drives with dependency injection: subscribe to an
`EventBus` keyed by `WsEventType`, or implement `ConnectionHook` and
`ResponseSink` and run everything through `McbeServerFacade`. The package never
owns a message broker or an LLM worker — those concerns are the host's.

**One-way capability model:** the SDK sends bridge requests from the Python host
to the Minecraft addon and receives responses. There is no inbound
capability-registry dispatch — the addon side owns all capability handling. The
`LegacyMcbeAiV1Profile` is the sole built-in protocol profile.

## Install

Editable install against the main-repo venv (`.venv` lives in the main repo, not
inside this package):

```bash
pip install -e ./mcbe-ws-sdk
```

## Quickstart

`McbeServerFacade` is the host entry point. Build one with default collaborators,
override them one at a time, then run it:

```python
import asyncio
from mcbe_ws_sdk import McbeServerFacade, ConnectionHook


class MyHook(ConnectionHook):
    async def on_connected(self, state):
        print("connected:", state.id)

    async def on_player_message(self, state, event):
        print("chat:", event.message)

    async def on_disconnected(self, state):
        print("disconnected:", state.id)


async def main() -> None:
    facade = McbeServerFacade(hook=MyHook())
    print(f"listening on ws://{facade.settings.websocket.host}:{facade.settings.websocket.port}")
    await facade.run_lifetime()


if __name__ == "__main__":
    asyncio.run(main())
```

The constructor is keyword-only; every argument collapses to a gateway default
when `None`, so `McbeServerFacade()` stands up a working facade with neutral
sink, empty command registry, and a default-safe addon bridge:

```python
facade = McbeServerFacade(
    settings=None,    # -> GatewaySettings()
    hook=None,        # -> NoOpHook()
    sink=None,        # -> DefaultResponseSink()
    addon=None,       # -> AddonBridgeService(settings.addon)
    registry=None,    # -> CommandRegistry()
)
```

Stop a running facade from another task with `await facade.stop()`
(`run_lifetime` unwinds cleanly into a graceful shutdown; cancelling the task
also works).

## Public API surface

A host implements / injects these classes:

- `ConnectionHook` (+ 6 hooks): `on_connected`, `on_disconnected`,
  `on_player_message`, `on_ui_chat_reassembled`, `on_command_response`,
  `on_error`.
- `ResponseSink` / `DefaultResponseSink`: outbound routes for text payloads and
  system notifications.
- `AddonBridgeService` + `AddonBridgeClient`: the ScriptEvent bridge carrying
  structured capability requests/responses (no global singleton).
- `LegacyMcbeAiV1Profile` (and module-level `LEGACY_MCBEAI_V1`): the one built-in
  protocol profile for legacy mcbeai v1 addon interop.
- `CommandRegistry`: the Minecraft command surface the protocol handler renders
  (empty by default).
- `ConnectionManager`: owns per-connection state and the player session map.
- `MinecraftProtocolHandler`: parses inbound traffic and builds outbound packets.
- `EventBus` / `WsEventType`: low-level typed event subscription.

`addon/service.py` and the addon bridge carry no global singleton and are
configured entirely through `AddonBridgeSettings`.

## Development

```bash
pip install -e ".[dev]"
ruff check --no-cache src tests examples
mypy --no-incremental src
pytest -p no:cacheprovider -q
```

## License

MIT

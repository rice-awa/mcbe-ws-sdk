# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)

Generic WebSocket gateway SDK for Minecraft Bedrock Edition. The package owns the
WS transport, packet protocol and byte-safe command chunking, and exposes a
dual-layer interface the host drives with dependency injection: subscribe to an
`EventBus` keyed by `WsEventType`, or implement `ConnectionHook` and
`ResponseSink` and run everything through `McbeServerFacade`. The package never
owns a message broker or an LLM worker — those concerns are the host's.

## Install

Editable install against the main-repo venv (`.venv` lives in the main repo, not
inside this package):

```bash
pip install -e ./mcbe-ws-sdk
```

## Quickstart

`McbeServerFacade` is the host entry point. Build one with all defaults
(`SilentResponseSink`), override collaborators one at a time, then run it:

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
when `None`, so `McbeServerFacade()` stands up a working facade with silent
sink, default command registry and capability registry:

```python
facade = McbeServerFacade(
    settings=None,        # -> GatewaySettings()
    hook=None,            # -> NoOpHook()
    sink=None,            # -> SilentResponseSink()
    addon=None,           # -> AddonBridgeService(settings.addon)
    registry=None,        # -> CommandRegistry(DEFAULT_COMMANDS)
    capabilities=None,    # -> CapabilityRegistry()
)
```

Stop a running facade from another task with `await facade.stop()`
(`run_lifetime` unwinds cleanly into a graceful shutdown; cancelling the task
also works).

## Public API surface

A host implements / injects these classes:

- `ConnectionHook` (+ 7 hooks): `on_connected`, `on_authenticated`,
  `on_disconnected`, `on_player_message`, `on_bridge_message`,
  `on_ui_chat_reassembled`, `on_command_response`.
- `ResponseSink` / `SilentResponseSink` / `DefaultResponseSink`: how outbound
  tellraw / scriptevent / AI-response payloads are delivered.
- `AddonBridgeService` + `AddonBridgeClient`: the ScriptEvent bridge carrying
  structured requests/responses (no global singleton).
- `CapabilityRegistry` + `CapabilityHandler` + `CapabilityContext`: override
  point for inbound `scriptevent mcbeai:bridge_request` calls.
- `CommandRegistry`: the Minecraft command surface the protocol handler renders.
- `ConnectionManager`: owns per-connection state and the player session map.
- `MinecraftProtocolHandler`: parses inbound traffic and builds outbound packets.
- `EventBus` / `WsEventType`: low-level typed event subscription.

`addon/service.py` and the addon bridge carry no global singleton and are
configured entirely through `AddonBridgeSettings`.

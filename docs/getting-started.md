# Getting Started

## Install

```bash
pip install mcbe-ws-sdk
```

Editable install for development:

```bash
pip install -e ".[dev,docs]"
```

## Minimal host

`McbeServerFacade` is the host entry point. Build one with default
collaborators, override them one at a time, then run it:

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
    print(
        f"listening on ws://{facade.settings.websocket.host}"
        f":{facade.settings.websocket.port}"
    )
    await facade.run_lifetime()


if __name__ == "__main__":
    asyncio.run(main())
```

## Defaults

The constructor is **keyword-only**. Every argument collapses to a gateway
default when `None`, so `McbeServerFacade()` stands up a working facade with a
neutral sink, empty command registry, and a default-safe addon bridge:

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

## What hosts usually inject

| Surface | Role |
|---------|------|
| `ConnectionHook` | Six lifecycle hooks (`on_connected`, `on_disconnected`, `on_player_message`, `on_ui_chat_reassembled`, `on_command_response`, `on_error`) |
| `ResponseSink` | Routes `OutboundText` / `SystemNotification` into Minecraft commands |
| `AddonBridgeService` | ScriptEvent capability request/response (no global singleton) |
| `CommandRegistry` | Prefix/alias command resolution (empty by default) |
| `MCBEWS_V1` | Built-in mcbews v1 protocol profile |

## Build these docs locally

```bash
pip install -e ".[docs]"
mkdocs serve          # EN  http://127.0.0.1:8000
                      # 中文 http://127.0.0.1:8000/zh/
mkdocs build --strict # writes ./site
```

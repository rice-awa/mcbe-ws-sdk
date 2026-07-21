# Getting Started

This guide assumes you are new to the SDK. By the end you will have:

1. Installed `mcbe-ws-sdk`
2. Connected Minecraft Bedrock to a local Python server
3. Echoed player chat back into the game with `tellraw`
4. Understood the two surfaces you implement: `ConnectionHook` and `ResponseSink`

---

## Prerequisites

| Item | Requirement |
|------|-------------|
| Python | **3.11+** (`python --version`) |
| OS | Windows / macOS / Linux |
| Minecraft | Bedrock (mobile / Win10·11 store / Education, …) with a world that allows commands |
| Network | Same machine or same LAN; firewall allows the listen port |

!!! note "How `/wsserver` works"
    Bedrock `/wsserver` is a **client** command. After the player types it, **that
    client** connects to your Python server. No dedicated server is required — a
    single-player world is enough.

---

## Install

### From PyPI

```bash
pip install mcbe-ws-sdk
```

### Editable install from source (recommended for development / examples)

```bash
cd mcbe-ws-sdk
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"
```

Verify:

```bash
python -c "import mcbe_ws_sdk; print(mcbe_ws_sdk.__version__)"
```

A version string (e.g. `0.1.0`) means install succeeded.

---

## 5-minute walkthrough: run the first example

The repo ships an “echo every chat message” example. Run it first, then write
your own host.

### Step 1 — start the Python server

From the `mcbe-ws-sdk` directory:

```bash
python examples/basic-server/server.py
```

You should see something like:

```text
[info] listening host=0.0.0.0 port=8080 url=ws://0.0.0.0:8080
[info] connect_hint command=/wsserver <this-machine-ip>:8080
[info] ready stop_with=Ctrl+C
```

Optional flags:

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
python examples/basic-server/server.py --log-level DEBUG   # frame-level detail
```

### Step 2 — find your IP

| Scenario | Type in game |
|----------|--------------|
| Game and Python on the **same machine** | `/wsserver 127.0.0.1:8080` |
| Game on a phone / another PC | `/wsserver 192.168.x.x:8080` (your LAN IP) |

```bash
# Linux / macOS
ip a          # or ifconfig
# Windows
ipconfig
```

Look for something like `192.168.1.23`. **Do not** give `127.0.0.1` to another device.

### Step 3 — connect from Minecraft

1. Open a Bedrock world (cheats / commands must be allowed).
2. In chat, type (replace the IP):

   ```text
   /wsserver 127.0.0.1:8080
   ```

3. On success:

   - the game shows a short notice (the example sends a welcome notification);
   - the Python terminal prints `connected`.

### Step 4 — send a message

Type anything in chat, e.g. `hello`.

- In-game you should get: `收到 <your-name> 的消息：hello`
- The terminal should log a `chat` line

`帮助` or `!help` prints the example help text.

### Step 5 — stop the server

Press `Ctrl+C` in the Python terminal for a clean shutdown.

---

## Write a minimal echo bot

Save this as `my_bot.py` and run it from any directory (after
`pip install mcbe-ws-sdk`).

```python
"""Minimal echo bot: reply to whatever the player says."""

import asyncio

from mcbe_ws_sdk import (
    DefaultResponseSink,
    FlowControlSettings,
    GatewaySettings,
    McbeOutboundDelivery,
    McbeServerFacade,
    NoOpHook,
    OutboundText,
    WebsocketTransportConfig,
    enqueue_response,
)


# ── 1. Outbound: turn SDK messages into in-game tellraw ──────
class MinecraftSink(DefaultResponseSink):
    def __init__(self, flow: FlowControlSettings) -> None:
        self._flow = flow

    def _delivery(self, state):
        if state.send_payload is None:
            return None
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            settings=self._flow,
        )

    async def on_outbound_text(self, state, message: OutboundText) -> None:
        delivery = self._delivery(state)
        if delivery is not None:
            await delivery.send_outbound_text(message)

    async def on_system_notification(self, state, message) -> None:
        delivery = self._delivery(state)
        if delivery is not None:
            await delivery.send_system_notification(message)


# ── 2. Inbound: subclass NoOpHook, override only what you need ─
class EchoHook(NoOpHook):
    async def on_connected(self, state) -> None:
        print(f"[+] connected: {state.id}")

    async def on_disconnected(self, state) -> None:
        print(f"[-] disconnected: {state.id}")

    async def on_player_message(self, state, event, parsed=None) -> None:
        # Bedrock echoes our own tellraw back with sender "外部" / "External"
        if event.sender in {"外部", "External"}:
            return

        text = event.message.strip()
        if not text:
            return

        print(f"[chat] {event.sender}: {text}")

        # Use event.sender as the target — one /wsserver can carry many players
        enqueue_response(
            state,
            OutboundText(
                content=f"got {event.sender}'s message: {text}",
                channel="echo",
                player_name=event.sender,
                target=event.sender,
            ),
        )


# ── 3. Boot ───────────────────────────────────────────────────
async def main() -> None:
    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(host="0.0.0.0", port=8080),
    )
    facade = McbeServerFacade(
        settings=settings,
        hook=EchoHook(),
        sink=MinecraftSink(settings.flow),
    )
    print("listening on ws://0.0.0.0:8080 — in game run /wsserver <this-ip>:8080")
    await facade.run_lifetime()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python my_bot.py
```

### What the pieces do

| Piece | Role |
|-------|------|
| `EchoHook(NoOpHook)` | Override only the hooks you care about: connect / disconnect / chat |
| `MinecraftSink` | Implements `ResponseSink`: turns `OutboundText` into real MC commands |
| `enqueue_response(...)` | Puts a reply on the connection’s send queue; an SDK coroutine delivers it |
| `McbeServerFacade` | One-stop entry: listen, handshake, subscribe to `PlayerMessage`, dispatch |
| `event.sender` | **Player identity**. Do not use `state.player_name` (one connection can have many players) |

!!! tip "Prefer `NoOpHook`"
    `ConnectionHook` is a protocol. Day-to-day, subclass `NoOpHook` and override
    only what you need — you don’t have to implement all six hooks.

---

## Defaults

The constructor is **keyword-only**. Every argument collapses to a gateway
default when `None`, so `McbeServerFacade()` stands up a working facade with a
neutral sink, empty command registry, and a default-safe addon bridge:

```python
facade = McbeServerFacade(
    settings=None,    # → GatewaySettings()
    hook=None,        # → NoOpHook()
    sink=None,        # → DefaultResponseSink()
    addon=None,       # → AddonBridgeService(settings.addon)
    registry=None,    # → CommandRegistry()
)
```

Stop a running facade from another task with `await facade.stop()`
(`run_lifetime` unwinds cleanly into a graceful shutdown; cancelling the task
also works).

---

## What hosts usually inject

| Surface | Role |
|---------|------|
| `ConnectionHook` | Six lifecycle hooks (`on_connected`, `on_disconnected`, `on_player_message`, `on_ui_chat_reassembled`, `on_command_response`, `on_error`) |
| `ResponseSink` | Routes `OutboundText` / `SystemNotification` into Minecraft commands |
| `AddonBridgeService` | ScriptEvent capability request/response (no global singleton) |
| `CommandRegistry` | Prefix/alias command resolution (empty by default) |
| `MCBEWS_V1` | Built-in mcbews v1 protocol profile |

### Dual interface

| Layer | How you use it | Best for |
|-------|----------------|----------|
| **High-level** | Implement `ConnectionHook` + `ResponseSink`, hand them to `McbeServerFacade` | Almost every host (recommended) |
| **Low-level** | Subscribe to `EventBus` (keyed by `WsEventType`) | When you need to assemble the lifecycle yourself |

### Multiplayer isolation

A Bedrock world usually has **one** `/wsserver` connection shared by many players.

- Bucket history / locks / context by `(connection_id, player_name)`
- Always take identity from `event.sender` on the current message — never from connection-level `ConnectionState.player_name`

---

## More examples

| Example | Path | What it shows |
|---------|------|---------------|
| Basic echo server | [`examples/basic-server/`](https://github.com/rice-awa/mcbe-ws-sdk/tree/main/examples/basic-server) | Minimal runnable host — start here |
| Addon capability demo | [`examples/addon-server/`](https://github.com/rice-awa/mcbe-ws-sdk/tree/main/examples/addon-server) | Chat commands for player / inventory + raw WS commands |
| In-memory capability round-trip | [`examples/addon-capability-call/`](https://github.com/rice-awa/mcbe-ws-sdk/tree/main/examples/addon-capability-call) | Bridge API without a live game |

### Calling in-game capabilities

Build and enable the companion addon first:

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

Import the generated `.mcaddon` into the world, enable the pack, then run
`examples/addon-server`.

!!! warning "Beta APIs required"
    In the world settings, open **Experiments** and turn on **Beta APIs**.
    This addon uses the Minecraft Script API; without Beta APIs the scripts
    never load and capability requests time out. Prefer a dedicated test world
    — experiments can affect achievements / features.

Details: the [addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md)
and the example’s own README.

---

## FAQ

**Game says connection failed?**

- Confirm Python is running and printed `listening`
- Port free? Firewall open?
- Cross-device: use the LAN IP, not `127.0.0.1`
- Phone and PC on the same Wi-Fi? AP isolation off?

**Connected but no reply to chat?**

- Does the Python terminal show a `chat` log?
- Only `external_echo_ignored`? That’s the filter for our own tellraw echo — normal
- Try `--log-level DEBUG` and check for `PlayerMessage`

**One message arrives as several pieces?**

- Expected. Bedrock’s `commandLine` safe budget is about **461 bytes**; the SDK
  chunks and paces sends automatically.

**Can multiple players use it at once?**

- Yes. Every player on the same `/wsserver` connection triggers
  `on_player_message`. Always address players via `event.sender`.

**How do I put this on the public internet safely?**

- The examples have **no auth**. Add login / permission checks in
  `ConnectionHook`, plus firewall and reverse proxy, before any public deploy.
  The addon bridge is **not** a security boundary.

**Can I `await` long work inside `on_player_message`?**

- **Don’t** block waiting (e.g. for an addon response or `commandResponse`).
  The facade processes inbound frames serially; blocking starves the receive
  loop. Spawn `asyncio.create_task(...)` and return immediately —
  `examples/addon-server` shows the pattern.

**Addon capability calls time out?**

- Is the bridge pack active in **this** world?
- Is **Experiments → Beta APIs** on? Scripts do not load without it.
- Is `/wsserver` still connected?

---

## Next steps

- [Architecture](architecture.md) — layer stack and dependency inversion
- [Protocol](addon-bridge-protocol.md) — mcbews v1 bridge wire format
- [API Reference](reference.md) — generated from source

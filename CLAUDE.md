# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`mcbe-ws-sdk` is a **generic, independently publishable** WebSocket gateway SDK for Minecraft Bedrock Edition. It was extracted from the parent MCBE-AI-Agent monorepo and deliberately owns **only** the WS transport, packet protocol, byte-safe command chunking, and addon bridge — no message broker, no LLM worker, no provider selection. The parent host application injects behaviour through two protocols: `ConnectionHook` and `ResponseSink`.

- **Python**: 3.11+ (tested 3.11–3.14)
- **Build**: hatchling (wheel + sdist)
- **Dependencies**: pydantic >= 2.0, structlog >= 24.0, websockets >= 12, < 17
- **Dev tools**: ruff (lint/format), mypy (strict mode), pytest + pytest-asyncio (auto mode)

## Common Commands

```bash
# Editable install with dev extras
pip install -e ".[dev]"

# Lint
ruff check --no-cache src tests examples

# Type check (strict mode; only src)
mypy --no-incremental src

# Run all tests
pytest -p no:cacheprovider -q

# Run a single test file / node
pytest tests/unit/test_flow_control.py
pytest tests/unit/test_flow_control.py -k test_chunk_tellraw

# Auto-format (Python via ruff; Addon via prettier when Node is available)
python tools/format.py
python tools/format.py --check
python tools/format.py --python
python tools/format.py --addon

# Build distributable artifacts
python -m build --sdist --wheel

# Validate distribution
twine check dist/*
python tools/check_dist.py dist

# Docs site (Material + mkdocstrings + static-i18n)
# English at / ; 中文 at /zh/
pip install -e ".[docs]"
mkdocs serve
mkdocs build --strict
```

## Documentation language

- **Public source docstrings** (modules, classes, public methods) are **English**. Prefer Google-style sections (`Args` / `Returns` / `Raises`) when structured detail is needed. Do not put dual-language EN+ZH blocks in source docstrings.
- **Narrative docs** for humans may be bilingual via suffix pairs: `docs/page.md` + `docs/page.zh.md`, `README.md` + `README.zh.md`.
- **API reference**: mkdocstrings generates English only (`docs/reference.md`). The Chinese site keeps a stub at `docs/reference.zh.md` that points readers at the English API page — do not re-run the same `:::` tree under `/zh/` (strict-mode cross-ref conflicts).
- Inline comments may stay English; game-facing default strings (welcome/error UI) may remain Chinese when they are product copy for Chinese players, not API docs.
- CJK may appear inside English docs only as **examples** (e.g. Bedrock player name ``玩家``) or quoted game error text.

## Architecture

The SDK layers stack bottom-up as follows. Every layer above transport is injectable so the host can override any default without the SDK importing host code:

```
McbeServerFacade          ← host entry point; owns the full WS lifetime
├── ConnectionManager     ← active connections + one response-sender coroutine per connection
│   ├── ConnectionState   ← transport-agnostic identity (id, send_payload, response_queue)
│   └── ResponseSink      ← protocol the host implements to route OutboundText / SystemNotification
├── MinecraftProtocolHandler  ← parse PlayerMessage, resolve typed commands, render status lines
│   └── CommandRegistry   ← prefix/alias → type matcher (host configures)
├── EventBus              ← typed in-process pub/sub keyed by WsEventType
├── ConnectionHook        ← protocol with 6 lifecycle hooks (host implements)
├── AddonBridgeService    ← ScriptEvent bridge: capability request/response with chunk reassembly
│   └── AddonBridgeSession ← per-connection pending futures + chunk buffers
├── FlowControlMiddleware  ← byte-safe tellraw/scriptevent chunking (461 B MCBE hard limit)
└── McbeOutboundDelivery   ← unified outbound adapter (tellraw, scriptevent, raw, legacy v1)
```

### Dependency inversion is the core pattern

`McbeServerFacade.__init__` is **keyword-only**; every collaborator collapses to a gateway default when `None`:

```python
facade = McbeServerFacade(
    settings=None,    # → GatewaySettings()
    hook=None,        # → NoOpHook()
    sink=None,        # → DefaultResponseSink()
    addon=None,       # → AddonBridgeService(settings.addon)
    registry=None,    # → CommandRegistry()
)
```

The host implements `ConnectionHook` (6 side-effecting hooks, all `-> None`: `on_connected`, `on_disconnected`, `on_player_message(state, event, parsed=None)`, `on_ui_chat_reassembled`, `on_command_response`, `on_error`) and a `ResponseSink` (routes `OutboundText` / `SystemNotification` → MC commands via `McbeOutboundDelivery`). `parsed` is an optional pre-parsed `ParsedCommand` from the registry, not a consumed-bool return. `NoOpHook` and `DefaultResponseSink` define the complete contract; a host subclasses only what it needs.

There is **no** SDK-owned `PlayerSession`. Multiplayer isolation is a **host** concern: bucket history / locks / context by `(connection_id, sender)` using `PlayerMessageEvent.sender`. `ConnectionState.player_name` is a deprecated convenience pointer only.

### Per-connection message routing flow

1. `McbeServerFacade._on_connection` — creates connection state, starts a `_response_sender` coroutine, sends handshake + subscribe; after those succeed, emits `CONNECTED` then calls `hook.on_connected` (welcome is host-owned — the facade never sends a welcome banner)
2. `_handle_raw` classifies each inbound frame:
   - **error** frame → emits `WsEventType.ERROR` + calls `hook.on_error`
   - **commandResponse** → emits `WsEventType.COMMAND_RESPONSE` + calls `hook.on_command_response`
   - **addon prefix match** → routes to `AddonBridgeService.handle_player_message` (bridge chunk reassembly or UI chat reassembly)
   - **PlayerMessage** → emits `WsEventType.PLAYER_MESSAGE` + calls `hook.on_player_message(state, event, parsed=...)`
3. The response-sender coroutine drains `state.response_queue`, wrapping each message via `RouteEnvelope.from_message()` and routing inline to the sink's two `on_*` methods (no `dispatch` on the protocol)
4. The host's `HostSink` turns queued messages into MC WebSocket payloads using `McbeOutboundDelivery`

### Protocol profiles

Protocol profiles live under `profiles/` and define wire-format constants for different addon interop layers. `McbewsV1Profile` is the sole built-in profile (module-level singleton `MCBEWS_V1`). The profile controls bridge message IDs, chat prefixes, simulated player name, chunk delays, and the text-response frame format (`mcbews:text_resp`). `AddonBridgeSettings` carries the profile reference so the addon service stays parameterised.

### Flow control — 461 B hard limit

`FlowControlMiddleware` enforces the MCBE `commandLine` byte budget (461 bytes, empirically determined). Methods:
- `chunk_tellraw()` / `chunk_scriptevent()` — semantically-aware sentence splitting with byte-safety guard
- `chunk_raw_command()` — no semantic splitting; raises `FrameTooLargeError` on overflow
- `chunk_framed_scriptevent()` — two-pass: split then re-encode with `i/n` index metadata
- `_assert_byte_safe()` — defensive guard on every outbound payload

### Addon bridge protocol

The bridge carries structured capability requests/responses over `scriptevent` via a simulated bridge player (`MCBEWS_BRIDGE`). Messages are piped through chat with a `namespace|prefix|request_id|index/total|content` format (`MCBEWS|BRIDGE`, `MCBEWS|UI_CHAT`). `AddonBridgeSession` handles chunk reassembly with TTL-based buffer expiry, byte limits, and per-connection future management. There is no global singleton — `AddonBridgeService` instances are constructed with explicit `AddonBridgeSettings`.

**World requirement:** the companion Script addon only loads when the Bedrock world has **Experiments → Beta APIs** enabled. Without it, scripts never load and capability requests time out. Document this whenever writing enable/import steps for the addon. See `addon/README.md` (Enable in a world).

**Trust boundary:** the bridge is not a security boundary. The host must authenticate/authorize capability callers; the addon only applies a defensive allow/denylist on the world-command path. See `addon/README.md` (Trust boundary).

### No host imports

The SDK never imports from its parent repo. `ConnectionState.send_payload` is an opaque `Callable[[str], Awaitable[None]]` — the facade wires it to `websocket.send`; the host can wrap it further. `MessageSurfaceConfig` is a frozen value object so the protocol handler doesn't import the host's `MinecraftConfig`. All host-specific framing (`connection_id`, `BaseMessage.id`, timestamps) stays in the host's own models; the gateway's `OutboundText` / `SystemNotification` are intentionally lean.

## Key Files

| File | Purpose |
|------|---------|
| `src/mcbe_ws_sdk/__init__.py` | Public API surface — every re-exported name |
| `src/mcbe_ws_sdk/gateway/server_facade.py` | `McbeServerFacade` — entry point, owns WS transport + protocol machinery |
| `src/mcbe_ws_sdk/gateway/connection.py` | `ConnectionManager` + `ConnectionState` — per-connection queues and sender coroutines |
| `src/mcbe_ws_sdk/gateway/handler.py` | `MinecraftProtocolHandler` — packet parsing, command resolution, status rendering |
| `src/mcbe_ws_sdk/gateway/events.py` | `EventBus` + `WsEventType` — typed in-process pub/sub |
| `src/mcbe_ws_sdk/gateway/hook.py` | `ConnectionHook` protocol + `NoOpHook` default (6 lifecycle hooks) |
| `src/mcbe_ws_sdk/gateway/sink.py` | `ResponseSink` protocol + `DefaultResponseSink` + `RouteEnvelope` |
| `src/mcbe_ws_sdk/gateway/messages.py` | `OutboundText` / `SystemNotification` value objects |
| `src/mcbe_ws_sdk/delivery/outbound.py` | `McbeOutboundDelivery` — unified tellraw/scriptevent/raw send adapter |
| `src/mcbe_ws_sdk/flow/flow_control.py` | `FlowControlMiddleware` — byte-safe command chunking |
| `src/mcbe_ws_sdk/command/registry.py` | `CommandRegistry` — prefix/alias command resolution |
| `src/mcbe_ws_sdk/protocol/minecraft.py` | Pydantic models: `MinecraftCommand`, `PlayerMessageEvent`, `MinecraftCommandResponse` |
| `src/mcbe_ws_sdk/addon/service.py` | `AddonBridgeService` + `AddonBridgeClient` — bridge request/response cycle |
| `src/mcbe_ws_sdk/addon/session.py` | `AddonBridgeSession` — per-connection pending futures + chunk buffer management |
| `src/mcbe_ws_sdk/profiles/mcbews_v1/profile.py` | `McbewsV1Profile` — wire-format constants |
| `src/mcbe_ws_sdk/profiles/mcbews_v1/codec.py` | Encode/decode bridge requests, UI chat chunks, text response frames |
| `src/mcbe_ws_sdk/profiles/mcbews_v1/delivery.py` | `McbewsV1Delivery` — send text responses with prelude+chunk delays |
| `src/mcbe_ws_sdk/config.py` | `GatewaySettings` — all frozen dataclass settings with validation |
| `src/mcbe_ws_sdk/errors.py` | Exception hierarchy (`McbeWsSdkError` → `ProtocolError`, `BridgeError`, `ConfigurationError`, etc.) |

## CI

- **quality** — ruff + mypy (strict, src only)
- **python** — pytest across Python 3.11–3.14
- **websockets** — test facade + delivery against websockets 12, 14, 16
- **addon** — Node.js build, test, lint, typecheck for the TypeScript addon side
- **docs** — `mkdocs build --strict` (Material + mkdocstrings + static-i18n, EN/中文); uploads `docs-site` artifact
- **docs-pages** — on `main` (docs/src/mkdocs paths) or `workflow_dispatch`: build + deploy to GitHub Pages (`https://rice-awa.github.io/mcbe-ws-sdk/`)
- **dist** — build sdist+wheel, twine check, check_dist, verify import (gated on all prior jobs)
- **release** — tag-triggered; verifies release tag matches package version

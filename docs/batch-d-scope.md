# Batch D — Scope Document (mcbe-ws-sdk)

**Package**: `mcbe-ws-sdk`, branch `main`.
**Goal of Batch D** (verbatim from the batch plan):
1. Create `gateway/server_facade.py` → `McbeServerFacade(*, settings, hook, sink, addon, registry, broker)` (None → defaults) + `run_lifetime(host, port)`.
2. Create `capability/registry.py` → `CapabilityContext`, `CapabilityHandler`, `CapabilityRegistry`, `LoggingStubHandler`.
3. Complete the `addon/service.py` refactor (no singleton, driven by `AddonBridgeSettings`).

Ground rules: facade must NOT own the LLM/MessageBroker worker (host concern); the addon bridge refactor must contain zero module-level mutable state. Below is the verified scope.

---

## A. `addon/service.py` — **DONE**

**Verdict: done.** The SDK copy `mcbe-ws-sdk/src/mcbe_ws_sdk/addon/service.py` meets every criterion; the only TODO is the host side (out of scope for the SDK).

**Evidence (read-only inspection):**
- `AddonBridgeService.__init__(self, settings: AddonBridgeSettings)` (line 46) — takes **explicit** `AddonBridgeSettings`; reads `settings.timeout_seconds` and `settings.protocol` (lines 49–50). No global settings read.
- Zero module-level mutable state. A repo-wide grep for `_addon_bridge_service`, `get_addon_bridge_service`, and `_protocol(` returns **only docstring/comment text** in `addon/service.py`, `addon/session.py`, and `addon/protocol.py` that *describes the old main-repo code* — none are actual definitions or calls.
- `protocol.py` codec functions take an explicit `protocol: AddonProtocolConfig | None` parameter (never a `_protocol()` global). `session.py` threads `AddonProtocolConfig` into every decode call.
- Dependency flow is fully inverted: service passes `self._protocol` into `encode_bridge_request(...)` and `is_*_message(...)` uses `self._protocol.*`. Per-connection sessions (`_sessions: dict[UUID, AddonBridgeSession]`) and a single optional `_ui_chat_callback`.
- `__init__.py` exports exactly `AddonBridgeClient`, `AddonBridgeService`.

**Remaining (host side, NOT the SDK's job):** the **main repo** `services/addon/service.py` still holds the old singleton (`_addon_bridge_service` global, `get_addon_bridge_service()` factory, `AddonBridgeService(timeout_seconds=...)` with a float, `_protocol()` global). The main repo's `WebSocketServer` (line 50) and `worker.py` (lines 864–868) still call `get_addon_bridge_service()`. Migrating the main repo onto the SDK's API is a subsequent host-side task.

---

## B. `McbeServerFacade` spec

High-level entry point that **owns the WS transport + connection/protocol machinery** and delegates all application behaviour to injected collaborators. Mirrors the current main-repo `WebSocketServer` orchestration (see `services/websocket/server.py`), but inverted: the server *receives* its pieces, it doesn't construct the host-only ones.

### B.1 Constructor signature

```python
class McbeServerFacade:
    def __init__(
        self,
        *,
        settings: GatewaySettings | None = None,
        hook: ConnectionHook | None = None,
        sink: ResponseSink | None = None,
        addon: AddonBridgeService | None = None,
        registry: CommandRegistry | None = None,
        capabilities: CapabilityRegistry | None = None,
    ) -> None:
```

Order note: the batch plan lists `broker` as the last positional kwarg, but the SDK deliberately does **not** own a broker. The host wires the broker via the injected `sink`/`hook`, so **`broker` is dropped from the facade constructor** (see B.4). If the plan's literal list must be kept for traceability, `broker` would be accepted-but-unused legacy; the cleaner reading is that `broker` is replaced by `capabilities`. **→ Open question.**

Each `None` → SDK default:
- `settings` → `GatewaySettings()`.
- `hook` → `NoOpHook()` (gateway default; see `gateway/hook.py`).
- `sink` → a default `ResponseSink` impl. **Decision needed:** `DefaultResponseSink` raises `NotImplementedError` on the three command routes and is described as "logging only" for the render routes — fine for tests/smoke but would blow up a naive host that doesn't override `GAME_MESSAGE`/`RUN_COMMAND`/`AI_RESPONSE_SYNC`. See Open Questions.
- `addon` → `AddonBridgeService(settings.addon)` (built from settings, so a no-arg facade still has a working bridge).
- `registry` → a `CommandRegistry` loaded with the canonical MCBE command set (the host currently has this mapping baked into `config`/template reload; the SDK needs at least the documented defaults — **→ Open question: where does the canonical command table live?**).
- `capabilities` → `CapabilityRegistry()` with a `LoggingStubHandler` fallback (the addons that ship with a default, so an un-configured facade doesn't KeyError).

Internally the facade constructs (and therefore owns the lifetime of):
- the `MinecraftProtocolHandler(registry, surface=MessageSurfaceConfig())` (see `gateway/handler.py`),
- the `ConnectionManager(sink=sink, event_bus=EventBus())` (see `gateway/connection.py`),
- a single `AddonBridgeService` instance (also reachable via `self.addon`), with `set_ui_chat_callback(...)` wired to the UI-chat path,
- the response-sender loops are owned per-connection inside `ConnectionManager._response_sender` (`connection.py` lines 163–196) — the facade does **not** create them.

### B.2 `async run_lifetime(host, port) -> None`

This is the facade's owned async context. It does:

1. **`await websockets.serve(self._handle_connection, host, port, ...)`** (transport config from `settings.websocket`: `max_size`, `max_queue`, ping timeouts live on `GatewaySettings.websocket`; the host currently reads these off `Settings.websocket` in `server.py` lines 65–74). The stored `Server` handle is closed on shutdown.
2. Register the per-connection WS protocol driver (`_handle_connection`) with `websockets`.
3. Block forever (until cancelled): e.g. `await asyncio.Event().wait()` / `await server.wait_closed()`. Return only on task cancellation or exception.
4. **Graceful shutdown** (mirrors `WebSocketServer.stop`, `server.py` lines 83–95): 
   - call `await self._connection_manager.shutdown_all()` (drops every connection, cancels each response-sender → emits `DISCONNECTED`; see `connection.py` lines 155–162),
   - `server.close()` then `await server.wait_closed()`.

Return semantics: on normal operation runs until cancelled; raises `OSError` on bind failure. **→ Open question: should `run_lifetime` be a plain awaitable-until-cancelled (idiomatic) or accept a stop `Event`/`timeout`?**

### B.3 Inbound message flow (what the facade actually does)

Per raw WS frame inside `_handle_connection`:

1. **`state = await connection_manager.create_connection(send_payload=websocket.send)`** → registers state, starts response-sender, emits `CONNECTED`. (Note: current `create_connection` does not emit `RAW_INBOUND`; the host hook `on_connected` is what fires welcome/subscribe — **→ Open question: who sends init/subscribe/welcome?**)
2. **`await hook.on_connected(state)`** — host hook sends `{"Result":"true"}`, the `subscribe PlayerMessage` payload, and welcome tellraw (today `server.py` does this *before* the message loop, lines 116–141).
3. **`async for message in websocket:`** → per frame:
   - emit `RAW_INBOUND` / raw log (mirror `server.py` raw logger, optional).
   - parse `MinecraftProtocolHandler.parse_player_message(data)` → `PlayerMessageEvent | None` (host does the reject-unknown-event dedup via `_is_external_duplicate_message` + `_handle_command_response` first; see `server.py` 164–213).
   - **Branch A — addon bridge response / UI chat**: if `addon.is_bridge_chat_message(sender, message)` or `addon.is_ui_chat_message(...)` → `addon.handle_player_message(state.id, sender, message)`; if UI chat reassembles to a message → fire `hook.on_ui_chat_reassembled(state, player_name, message)` and the host routes it like a chat (`server.py` `_handle_ui_chat_reassembled`). Return.
   - **Branch B — inbound capability request**: if the `PlayerMessageEvent.message` parses as a `scriptevent mcbeai:bridge_request` → build `AddonBridgeRequest` via `parse_addon_bridge_request(message, settings.addon.protocol.bridge_message_id)` &nbsp;(`protocol/addon.py`), then `await hook.on_bridge_message(state, request)`. This is the **host's override point** — where the capability registry gets invoked (see Section C). Return (response is shipped back asynchronously by the hook/registry).
   - **Branch C — command response**: if frame is `commandResponse` → resolve `state.pending_command_futures` (mirror `server.py` `_handle_command_response`) and `await hook.on_command_response(state, request_id, response)`. Return.
   - **Branch D — player command/chat**: `parsed = handler.parse_typed_command(event.message)` → if None, drop/ignore; else `await hook.on_player_message(state, event)` returning bool. Host consumes the rest (login, chat→broker, context, etc.).
4. On `ConnectionClosed`: `finally:` → `addon.close_connection(state.id)` then `await connection_manager.drop_connection(state.id)` → emits `DISCONNECTED`; the host hook `on_disconnected` clears per-player session state.

**Outbound** (responses) do **not** flow through the facade. A `ConnectionState.response_queue` is fed by the host (and is how the worker pushes `StreamChunk`/`SystemNotification`/command dicts). The existing `_response_sender` loop classifies each with `RouteEnvelope.from_message` (`sink.py` lines 46–67) and dispatches to the shared `ResponseSink` (`sink.py` `dispatch`, lines 158–169). The three command routes (`GAME_MESSAGE`, `RUN_COMMAND`, `AI_RESPONSE_SYNC`) are intentionally host-only (`DefaultResponseSink` raises `NotImplementedError`).

### B.4 What the facade must NOT own (host stays host-side)

Everything that today lives in the main repo's `WebSocketServer`, `worker.py`, and `AgentDependencies`:

- `MessageBroker` / `core.queue` — the request queue, `submit_request`, `ensure_conversation`, history, the conversation lock/epoch machinery. **Not touched.**
- The **LLM worker** (`services/agent/worker.py`): builds `AgentDependencies` (lines 208–218), the PydanticAI `Agent` (provider registry, `http_client`, `wrap_registered_tools`), and pushes `StreamChunk`/`SystemNotification` into `broker.send_response`. **Not touched.**
- `run_command` callback bridging a `commandResponse` future (`worker.py` `_create_command_callback`, lines ~840–860) and the per-connection `pending_command_futures` dict. The *gateways* response loop already resolves these (Branch C above), but the **command execution future plumbing** is the host's; the facade only exposes the hook/queue seam.
- Auth/login, JWT, prompt/context/per-player `PlayerSession` settings, model/template registry, MCP manager, conversation management. These are exactly `hook.on_authenticated` / `on_player_message` concerns — the facade surface is the hook, it never implements them.
- Provider selection and the actual MCBE **command building** (tellraw/ScriptEvent serialization into commandLine) on outbound — that's the host `HostSink`, delivered via the injected `sink`.

In short: the **facade owns accepting WS connections, the `ConnectionState`/`ConnectionManager` lifecycle, the response-sender routing over the sink, the addon-bridge session lifecycle, and command *parsing*. The host owns everything that talks to an LLM or mutates the world.**

### B.5 Textual wiring diagram

```
[ MCBE client websocket ]
        │  raw text frame
        ▼
 _handle_connection(websocket)
   ├── ConnectionManager.create_connection(send_payload=websocket.send)
   │     emits CONNECTED  ──► EventBus
   ├── hook.on_connected(state)             ◄── host sends init / subscribe / welcome
   └── async for message in websocket:
         │
         ├── raw log (optional)
         ├── commandResponse? ──► resolve pending_command_futures
         │                         + hook.on_command_response(...)
         │
         ├── parse_player_message() ──► PlayerMessageEvent
         │     │
         │     ├── addon.is_bridge_chat_message / is_ui_chat_message
         │     │     └─► addon.handle_player_message(...)
         │     │           └─► [UI_CHAT reassembled] ─► hook.on_ui_chat_reassembled(...)
         │     │
         │     ├── message parses as scriptevent mcbeai:bridge_request
         │     │     └─► AddonBridgeRequest via parse_addon_bridge_request()
         │     │           └─► hook.on_bridge_message(state, request)   ◄── host CapabilityRegistry
         │     │                 (capability handle() → dict, addon bridge encodes+ships back)
         │     │
         │     └── command/chat ─► parse_typed_command()
         │           └─► hook.on_player_message(state, event)  ◄── host login/chat/context/…
         │
         └── (response path, reverse, host-driven)
               state.response_queue  ◄── host/worker pushes StreamChunk / Notification / dicts
                     │
                     ▼  ConnectionManager._response_sender
               RouteEnvelope.from_message   (sink.py)
                     │
                     ▼  sink.dispatch
               STREAM_CHUNK / SYSTEM_NOTIFICATION ─► DefaultResponseSink renders (tellraw/scriptevent)
               GAME_MESSAGE / RUN_COMMAND / AI_RESPONSE_SYNC  ─► HostSink ─► broker.run_command
                     (enqueued to MessageBroker, NOT owned here)
 finally:
   addon.close_connection(state.id) ; ConnectionManager.drop_connection(state.id) → emits DISCONNECTED
```

---

## C. `CapabilityRegistry` spec

The seam the host uses to answer **inbound** `scriptevent mcbeai:bridge_request` capability calls (addon→Python direction), the mirror of the outbound `AddonBridgeClient.request(capability, payload)` tools.

### C.1 How the mapping works

`gateway/hook.py::ConnectionHook.on_bridge_message(state, request: AddonBridgeRequest) -> bool` is the host entry. The host implements it as:

```python
async def on_bridge_message(self, state, request) -> bool:
    ctx = CapabilityContext(
        connection_id=state.id,
        player_name=state.player_name,
        capability=request.capability,
        payload=request.payload,
        request_id=request.request_id,
        send=state.send_payload,            # transport frame-send back-reference
    )
    response_payload = await self.capability_registry.handle(ctx)   # -> dict
    # host-side addon-bridge service then encodes + ships the response back
    return True    # consumed
```

This mirrors how the in-game **simulator** defines capability semantics today (`tools_mcbe_simulator.py::AddonBridgeSimulator.handle_request`, lines 205–230): inbound `{request_id, capability, payload}` → handler returns a `dict` payload → simulator emits `MCBEAI|RESP|<request_id>|1/1|<json dict>>` on the `MCBEAI_TOOL` player. So **`CapabilityHandler.handle` returns the `dict` that becomes the `AddonBridgeResponse.payload`**, and the existing `addon/protocol.encode_*` + chunk reassembly machinery (`protocol/addon.py`, `addon/session.py`) carries it back. The `get_*_snapshot` / `find_entities` / `run_world_command` tools (`services/agent/tools.py`, lines 570–650) are precisely the host-provided capability set, statically expressible as `CapabilityHandler` registrations.

### C.2 `CapabilityContext` (suggested)

Dataclass holding everything a capability needs to act and reply:
- `connection_id: UUID`
- `player_name: str | None`
- `capability: str`
- `payload: dict[str, Any]`
- `request_id: str` (so the host can correlate the `MCBEAI|RESP` reply)
- `send: Callable[[str], Awaitable[None]]` (back-reference to the transport send)

### C.3 `CapabilityHandler` + `CapabilityRegistry` + `LoggingStubHandler`

- `CapabilityHandler` (Protocol): `async def handle(self, ctx: CapabilityContext) -> dict[str, Any]: ...`
- `CapabilityRegistry`: `register(capability: str, handler: CapabilityHandler)`; `async def handle(self, ctx: CapabilityContext) -> dict[str, Any]` — resolves by `ctx.capability`; if no handler registered, invoke the **default stub** and return `{"ok": False, "error": f"unsupported capability: {ctx.capability}"}`.
- `LoggingStubHandler`: the gateway default registered into an empty registry — logs a warning and returns the unsupported-error dict. Mirrors `DefaultResponseSink` / `NoOpHook`: a facade constructed with no host capability registry still behaves (logs + returns a well-formed error payload instead of raising/KeyErroring). Exactly parallel to how `DefaultResponseSink.on_stream_chunk` renders visibly so tests exercise the protocol.

✅ Yes — a default facade plus `LoggingStubHandler` should "still work": an inbound bridge request hits `NoOpHook.on_bridge_message` returning `False`... but the **facade** is what first routes to `hook.on_bridge_message`; once the host overrides it, the registry becomes the host seam. The stub guarantees a host that doesn't register a registry gets logged errors rather than `KeyError`. **→ Open question (see E): should the facade itself contain a tiny built-in response sender so the stub's returned dict is auto-shipped back as a `MCBEAI|RESP`, or must the host always re-ship via the addon service?**

---

## D. Recommended sub-agent split & sequencing

**1. Sub-agent A — `capability/registry.py` + tests (INDEPENDENT, do first).**
Produces `CapabilityContext`, `CapabilityHandler` (Protocol), `CapabilityRegistry`, `LoggingStubHandler`, add `capability/__init__.py`, tests in `tests/unit/test_*.py` (registry dispatch, default stub for unknown, `CapabilityHandler` protocol check). Depends on nothing in this batch. It mirrors the established test convention (`tests/unit/test_hook.py`, `test_sink.py`) and the `default-stub` pattern, so it is self-contained and can validate in isolation.

**2. Sub-agent B — `gateway/server_facade.py` + tests (depends on A for the capability registry seam, parallel-able only after A's contract exists).**
Builds `McbeServerFacade` + `run_lifetime`, imports from `gateway/...`, `addon/service.py`, `protocol/addon.py::parse_addon_bridge_request`, and `capability/registry.py`. Tests stand up a facade with `NoOpHook` + `LoggingStubHandler` + `InMemorySink`, drive a frame through a fake transport (mirror `test_connection_manager.py`'s `_send_noop` + `RecordingSink`/`EventBus` fixtures), assert CONNECTED/DISCONNECTED, command parsing, bridge-request → addon, and graceful shutdown. **B should not start until A's public names are frozen**, because B imports `CapabilityRegistry` and the `CapabilityContext` shape.

**3. Sub-agent C — top-level exports + docs (AFTER A and B).**
Update `gateway/__init__.py` to export `McbeServerFacade` (the docstring already references `...server_facade.McbeServerFacade`), add a `gateway/server_facade` import, `README.md` quickstart, and the batch-D changelog. Touched only after the two code modules land so exports are accurate.

**Justification of ordering:** A produces the smallest contract and the new-independent-module token; B depends on A's API but on nothing else in the batch; C is pure integration/documentation of A+B. A and C are trivially independent; B is the load-bearing piece that stitches every existing layer (hook/sink/connection/addon/events/handler/registry) together, so it goes last and is where integration surprises surface — with tests from A and the existing suite as a regression net.

---

## E. Open questions (orchestrator decisions needed)

1. **`broker` in the constructor list.** The plan has `broker` as the last kwarg, but the SDK must not own a broker (B.4). Resolve: **(a)** drop `broker` and add `capabilities: CapabilityRegistry | None`, or **(b)** keep `broker` as deprecated/no-op for traceability? Recommendation: **(a)**, rename to `capabilities`.
2. **Default sink behaviour.** `DefaultResponseSink` raises `NotImplementedError` on `GAME_MESSAGE`/`RUN_COMMAND`/`AI_RESPONSE_SYNC` and only logs on the render routes. A naive host that injects only a hook would get exceptions. Resolve: ship a `SilentDefaultSink` that logs + no-ops on the three command routes (instead of raising) as `sink` default, mirroring `NoOpHook`? Or require every host to supply a sink? Recommendation: a non-crashing default sink + doc.
3. **Canonical command table.** The facade builds a default `CommandRegistry`, but the SDK has no static command map yet. Resolve: embed a documented default table (matching the host's `chat/context/conversation/template/setting/mcp/ai_broadcast/switch_model/help/save/run_command/login`), or ship an empty default and require the host to pass `registry`? Recommendation: embed a sensible default + allow override; capture the table in `config.py`.
4. **Init/subscribe/welcome ownership.** Today the main-repo server sends `{"Result":"true"}`, the `subscribe PlayerMessage` payload, and the welcome tellraw *before* the message loop (`server.py` lines 116–141) — all host-specific strings. Resolve: move these into `hook.on_connected(state)` (facade just calls the hook), or keep small facade helpers? Recommendation: call `hook.on_connected(state)` only; host does the framing.
5. **`CapabilityContext` contents.** Confirm the fields (C.2) above, especially whether `send` belongs on the context (auto-ship replies) or the host must re-ship via its own `AddonBridgeService`. Tie-breaker influences whether `CapabilitiesRegistry` returns only a dict or also writes the response.
6. **Inbound bridge-request routing.** Resolve: is `hook.on_bridge_message` the *only* host override point for inbound bridge requests (with the host's impl delegating to its `CapabilityRegistry`), and does the gateway provide no built-in capability execution? Recommendation: yes — the gateway provides only the parse + hook seam; the registry lives in `capability/` and is host-driven. Stub is safety net only.
7. **`run_lifetime` cancellation model.** Plain "run until cancelled" (idiomatic, blocks on `server.wait_closed()`/an internal `Event`), vs. an explicit `stop: asyncio.Event` or timeout parameter. Idiomatic is simplest; the host can cancel the task.
8. **Main-repo migration (out of THIS scope, but flagged).** The main repo (`services/addon/service.py`, `services/websocket/server.py`, `services/agent/worker.py`) still uses the **old singleton** API and does not import `mcbe-ws-sdk`. It does **not today wire inbound `mcbeai:bridge_request` → capability calls at all** (the only inbound-capability-call implementation is in `tools_mcbe_simulator.py`). Migrating the main repo onto the SDK — and thereby realizing inbound capability handling — is a follow-up host-side task, not part of Batch D.

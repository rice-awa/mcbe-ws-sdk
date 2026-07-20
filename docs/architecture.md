# Architecture

The SDK layers stack bottom-up. Every layer above transport is injectable so
the host can override any default without the SDK importing host code:

```text
McbeServerFacade          ← host entry point; owns the full WS lifetime
├── ConnectionManager     ← active connections + one response-sender per connection
│   ├── ConnectionState   ← transport-agnostic identity (id, send_payload, queue)
│   └── ResponseSink      ← host routes OutboundText / SystemNotification
├── MinecraftProtocolHandler  ← parse PlayerMessage, resolve commands, status lines
│   └── CommandRegistry
├── EventBus              ← typed in-process pub/sub keyed by WsEventType
├── ConnectionHook        ← six lifecycle hooks the host implements
├── AddonBridgeService    ← ScriptEvent bridge + chunk reassembly
│   └── AddonBridgeSession
├── FlowControlMiddleware ← byte-safe tellraw/scriptevent chunking (461 B limit)
└── McbeOutboundDelivery  ← unified outbound adapter
```

## Dependency inversion

`McbeServerFacade.__init__` is keyword-only; every collaborator collapses to a
gateway default when `None`. The host subclasses only what it needs:

- `NoOpHook` / `ConnectionHook`
- `DefaultResponseSink` / `ResponseSink`

## Per-connection message routing

1. `McbeServerFacade._on_connection` creates connection state, starts a
   `_response_sender` coroutine, and sends subscribe + welcome.
2. `_handle_raw` classifies each inbound frame:
   - **error** → `WsEventType.ERROR` + `hook.on_error`
   - **commandResponse** → `WsEventType.COMMAND_RESPONSE` + `hook.on_command_response`
   - **addon prefix match** → `AddonBridgeService` (bridge / UI chat reassembly)
   - **PlayerMessage** → `WsEventType.PLAYER_MESSAGE` + `hook.on_player_message`
3. The response-sender drains `state.response_queue`, wrapping each message via
   `RouteEnvelope.from_message()` and dispatching through the sink.
4. The host sink turns queued messages into MC WebSocket payloads with
   `McbeOutboundDelivery`.

## Flow control — 461 B hard limit

`FlowControlMiddleware` enforces the MCBE `commandLine` byte budget (461 bytes,
empirically determined):

- `chunk_tellraw()` / `chunk_scriptevent()` — sentence-aware splitting with a
  byte-safety guard
- `chunk_raw_command()` — no semantic splitting; raises `FrameTooLargeError` on
  overflow
- `chunk_framed_scriptevent()` — two-pass split then re-encode with `i/n` metadata

## Protocol profiles

Protocol profiles live under `profiles/` and define wire-format constants.
`McbewsV1Profile` is the sole built-in profile (module-level singleton
`MCBEWS_V1`). See [Protocol](addon-bridge-protocol.md) for the wire format.

## No host imports

The SDK never imports from its parent host application.
`ConnectionState.send_payload` is an opaque
`Callable[[str], Awaitable[None]]` — the facade wires it to `websocket.send`.
Host-specific framing (`connection_id`, timestamps, LLM message ids) stays in
the host.

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

There is **no** SDK-owned `PlayerSession`. Multiplayer isolation is a **host**
concern: the gateway only forwards `PlayerMessageEvent.sender`; the host buckets
history / locks / context by `(connection_id, sender)`.

## Dependency inversion

`McbeServerFacade.__init__` is keyword-only; every collaborator collapses to a
gateway default when `None`. The host subclasses only what it needs:

- `NoOpHook` / `ConnectionHook`
- `DefaultResponseSink` / `ResponseSink`

`ConnectionHook` has six side-effecting hooks (all `-> None`). The chat hook
signature is:

```python
async def on_player_message(
    self,
    state: ConnectionState,
    player_event: PlayerMessageEvent,
    parsed: ParsedCommand | None = None,
) -> None: ...
```

`parsed` is the registry match when one exists; it is **not** a consumed-bool
return value — the host decides what to do with free-form chat and commands.

## Per-connection message routing

1. `McbeServerFacade._on_connection` creates connection state, starts a
   `_response_sender` coroutine, and sends handshake + subscribe. After those
   succeed it emits `WsEventType.CONNECTED` then calls `hook.on_connected`
   (welcome is the host's responsibility in that hook — the facade never sends
   a welcome banner itself).
2. `_handle_raw` classifies each inbound frame:
   - **error** → `WsEventType.ERROR` + `hook.on_error`
   - **commandResponse** → `WsEventType.COMMAND_RESPONSE` + `hook.on_command_response`
   - **addon prefix match** → `AddonBridgeService` (bridge / UI chat reassembly)
   - **PlayerMessage** → `WsEventType.PLAYER_MESSAGE` +
     `hook.on_player_message(state, event, parsed=...)`
3. The response-sender drains `state.response_queue`, wrapping each message via
   `RouteEnvelope.from_message()` and routing inline to the sink's two `on_*`
   methods (no `dispatch` on the protocol).
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

## Addon bridge trust boundary

The bridge is **not** a security boundary. The host must authenticate and
authorize who can invoke capabilities; the addon only applies a defensive
command allow/denylist on the world-command path. See
[addon/README.md — Trust boundary](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md#trust-boundary).

## No host imports

The SDK never imports from its parent host application.
`ConnectionState.send_payload` is an opaque
`Callable[[str], Awaitable[None]]` — the facade wires it to `websocket.send`.
Host-specific framing (`connection_id`, timestamps, LLM message ids) stays in
the host.

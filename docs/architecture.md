# Architecture

The SDK is a generic WebSocket gateway. It owns the transport, negotiation,
request correlation, task lifetimes, fragmentation and byte-safe delivery.
Your host owns authorization, history and business state.

```text
McbeServer                        only public entry
├── ConnectionManager / ConnectionState
│   └── connection state machine
├── ConnectionRuntime             bounded business + observation lanes
│   └── OutboundScheduler         per-connection send capacity and fairness
├── NativeCommandClient / RequestTable
├── MinecraftProtocolHandler / CommandRegistry
├── ConnectionHook / ResponseSink  host behavior and rendering
├── V2Bridge                       negotiation, requests, notifications, streams
│   └── V2Endpoint                 per-connection epoch and dispatch
├── McbeOutboundDelivery
└── FlowControlMiddleware          byte-safe tellraw/scriptevent chunking

Companion addon
router (mcbews:negotiate_v2 / mcbews:packet_v2)
└── V2Endpoint (capability registry + stream registry)
```

## Entry and ownership

`McbeServer` owns each connection from transport setup through protocol
negotiation, business processing and shutdown. Managed business lanes keep the
reader free while callbacks await replies. To render `OutboundText` or
`SystemNotification` in Minecraft, supply a `ResponseSink`; the default sink
only logs messages.

The SDK owns transport, negotiation, request waiters, task lifetimes, ordering and
fragment state. The host owns authentication, authorization, history and
business state. There is no SDK-owned `PlayerSession`; isolate application state
by `(connection_id, sender)` using the current `PlayerMessageEvent.sender`.

## Connection state machine

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

- The reader and required event subscriptions start before negotiation. The
  server never awaits negotiation before it can read replies.
- `NEGOTIATING` accepts negotiation frames, required control frames and
  correlated native `commandResponse` / `error` frames. Business frames are
  dropped and counted; they are not queued for replay.
- `READY_V2` is the only point where the server publishes business
  `CONNECTED`, calls `on_connected` and routes player messages.
- A timeout, malformed negotiation, busy negotiation, confirmation failure or
  invalid session reaches `REJECTED`, then `CLOSING` / `CLOSED`. Close code is
  `1008`; the reason token is bounded.
- A rejected connection has no paired business `on_connected` /
  `on_disconnected`. Transport diagnostics still record the attempt.
- A late or stale epoch cannot reopen a closed connection. Reconnect starts a
  new negotiation and a new epoch.

The addon accepts only `mcbews:negotiate_v2` and `mcbews:packet_v2`. Old
`mcbews:bridge_req` / `session_req` / `text_resp` events are logged and rejected
with zero business side effects. The addon cannot close a WebSocket owned by an
old server; it only refuses that server's business.

## Managed dispatch

Setup completes transport handshake and required subscriptions before the
negotiation result. After `READY_V2`, the reader classifies and correlates command
replies, negotiation frames and reserved carrier traffic before queuing business
callbacks.

Same-player chat stays FIFO across interceptors and the hook; different players
can progress concurrently. Other native business events share a FIFO lane.
Observers use a separate bounded lane. Business or observation overload rejects
work explicitly instead of growing without bound. Callback exceptions are
isolated.

Awaiting a command or capability inside a managed callback leaves the reader
free to receive its reply. Disconnect cancels and joins managed work, fails
waiters and releases buffers. Hosts do not create request wait tables, request
tasks or fragment buffers.

Hooks return `None`. `on_player_message(state, event, parsed=None)` receives an
optional registry match, not a consumed flag. The five lifecycle hooks are
`on_connected`, `on_disconnected`, `on_player_message`, `on_command_response`
and `on_error`.

## Bridge and delivery

`V2Bridge` owns the per-connection negotiation, capability registry and stream
registry. `V2Endpoint` holds one active epoch: it pairs replies before
dispatching requests, enforces request/stream limits, and carries metadata. A
capability handler receives `(name, payload, CapabilityContext)` and returns a
JSON mapping. Streams use `stream/1` and yield logical items; large items are
fragmented by the transport layer and delivered once.

The outbound scheduler is per connection. It bounds queued frames and bytes,
reserves control capacity, and schedules bulk content fairly. Hosts can pass
`state.send_bulk` to `McbeOutboundDelivery` to keep long text off the request
path.

The final command-line budget defaults to 461 UTF-8 bytes and may only be
lowered. tellraw and framed scriptevents can be split; native commands remain
atomic and reject overflow. MCBEWS/2 uplink measures the complete carrier frame
independently and does not inherit the downlink budget.

All wire formats, limits and error codes are defined by the installed
`profiles/mcbews_v2/manifest.json` and `vectors.json`. See
[the protocol contract](protocol-v2.md).

## Addon requirements and trust

The companion addon needs **Experiments → Beta APIs** in the target world.
Without it scripts do not load and negotiation times out. See the
[addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md).

Carrier attribution, the simulated `MCBEWS_BRIDGE` player and metadata identity
are not authentication boundaries. Hosts must authenticate and authorize
callers. Automated protocol coverage does not expand live-game platform or
version support.

The SDK imports no host application modules. `ConnectionState.send_payload` is
an opaque async callable wired to the transport; host-specific message IDs,
timestamps and business framing stay outside the SDK.

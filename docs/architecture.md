# Architecture

The SDK is a general-purpose WebSocket gateway. It takes care of the transport,
protocol negotiation, matching replies to requests, task lifetimes,
fragmentation and byte-safe delivery. Your host takes care of authorization,
history and business state.

```text
McbeServer                        only public entry
├── ConnectionManager / ConnectionState
│   └── connection state machine
├── ConnectionRuntime             bounded business + observation queues
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

`McbeServer` looks after each connection from transport setup through protocol
negotiation and business processing to shutdown. Business callbacks run on
queues that the SDK manages, so the reader stays free to receive replies while a
callback is waiting for one. To show `OutboundText` or `SystemNotification` in
Minecraft, supply your own `ResponseSink`; the default sink only logs them.

The SDK handles transport, negotiation, pending requests, task lifetimes,
ordering and fragment state. The host handles authentication, authorization,
history and business state. The SDK has no `PlayerSession` of its own, so keep
application state separate per `(connection_id, sender)`, using the value of the
current `PlayerMessageEvent.sender`.

## Connection state machine

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

- The reader and the required event subscriptions start before negotiation
  begins, so the server can always read replies while it waits for negotiation
  to finish.
- While in `NEGOTIATING`, the server accepts only negotiation frames, required
  control frames, and native `commandResponse` / `error` frames that match a
  pending request. Business frames are dropped and counted rather than queued
  for replay.
- Only in `READY_V2` does the server publish the business `CONNECTED` event,
  call `on_connected` and route player messages.
- A timeout, a malformed or busy negotiation, a failed confirmation or an
  invalid session moves the connection to `REJECTED`, then `CLOSING` /
  `CLOSED`. The close code is `1008`, and the reason is a short token with a
  length limit.
- A rejected connection never produces the business `on_connected` /
  `on_disconnected` pair, but transport diagnostics still record the attempt.
- A late or stale epoch (the identifier of one negotiated session) cannot reopen
  a closed connection. Reconnecting starts a new negotiation and a new epoch.

The addon accepts only `mcbews:negotiate_v2` and `mcbews:packet_v2`. It logs and
rejects the old `mcbews:bridge_req`, `session_req` and `text_resp` events
without triggering any business logic. It cannot close a WebSocket that belongs
to an old server, so it simply refuses that server's business frames.

## Managed dispatch

During setup, the SDK finishes the transport handshake and the required
subscriptions before it acts on the negotiation result. Once the connection
reaches `READY_V2`, the reader first classifies each frame and matches command
replies, negotiation frames and reserved carrier traffic, and only then queues
business callbacks.

Chat from the same player is handled first-in, first-out, through the
interceptors and then the hook, while different players can progress
concurrently. Other native business events share one first-in, first-out queue.
Observers have their own queue with a size limit. When the business or
observation queue is overloaded, the SDK rejects the new work explicitly instead
of letting the queue grow without limit. An exception in one callback is
contained and does not affect the others.

When a callback awaits a command or capability, the reader remains free to
receive the reply. On disconnect, the SDK cancels and waits for its managed
tasks, fails any pending requests and releases buffers. Your host does not need
to create its own table of pending requests, request tasks or fragment buffers.

Hooks return `None`. `on_player_message(state, event, parsed=None)` receives an
optional match from the command registry; it is not a flag saying the message
was consumed. The five hooks are `on_connected`, `on_disconnected`,
`on_player_message`, `on_command_response` and `on_error`.

## Bridge and delivery

`V2Bridge` manages negotiation, the capability registry and the stream registry
for each connection. `V2Endpoint` represents one active session (its epoch): it
matches replies to requests before dispatching new requests, enforces the
request and stream limits, and carries metadata. A capability handler receives
`(name, payload, CapabilityContext)` and returns a JSON mapping. Streams use
`stream/1` and produce logical items one at a time; the transport layer
fragments large items and delivers each one once.

Each connection has its own outbound scheduler. It limits how many frames and
bytes can be queued, reserves capacity for control frames, and shares the send
capacity fairly among bulk content. To keep long text from blocking requests,
pass `state.send_bulk` to `McbeOutboundDelivery`.

The budget for the final command line defaults to 461 UTF-8 bytes and can only
be lowered. tellraw and framed scriptevents may be split across several
commands. Native commands are never split and fail if they exceed the budget.
MCBEWS/2 uplink measures the whole carrier frame on its own and does not inherit
the downlink budget.

The installed `profiles/mcbews_v2/manifest.json` and `vectors.json` define every
wire format, limit and error code. See [the MCBEWS/2 protocol](protocol-v2.md).

## Addon requirements and trust

The companion addon needs **Experiments → Beta APIs** enabled in the target
world. The setup steps, and what happens if it is left off, are in the
[addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md).

The carrier that delivered a message, the simulated `MCBEWS_BRIDGE` player and
the identity in metadata are not authentication. Your host must authenticate and
authorize callers itself. Passing the automated protocol tests also does not
widen the game versions or platforms supported in live play.

The SDK never imports host application modules. `ConnectionState.send_payload`
is an opaque async callable connected to the transport; host-specific message
IDs, timestamps and business framing stay outside the SDK.

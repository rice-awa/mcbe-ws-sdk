# Architecture

This describes the unreleased checkout. See [migration](migration.md) for the
PyPI `0.2.1` boundary and unchanged legacy defaults.

```text
McbeServer               new entry: reliable dispatch + V2Bridge
└── McbeServerFacade      transport lifetime and injected collaborators
    ├── ConnectionManager / ConnectionState
    ├── ConnectionRuntime: bounded business/observation lanes and sender
    ├── NativeCommandClient / RequestTable: reply correlation and deadlines
    ├── MinecraftProtocolHandler / CommandRegistry
    ├── ConnectionHook / ResponseSink: host behavior and rendering
    ├── AddonBridgeService: v1 capabilities and optional business extensions
    ├── V2Bridge: discovery, bidirectional capabilities, metadata and streams
    └── McbeOutboundDelivery / FlowControlMiddleware: byte-safe delivery
```

## Entry points and ownership

`McbeServer` preserves the facade's keyword-only injection and lifecycle APIs,
selects reliable dispatch and supplies a v2 bridge. `McbeServerFacade()` retains
its legacy defaults; reliable mode and v2 can also be injected explicitly there.
The default response sink is neutral, so hosts supply rendering when they want
to turn `OutboundText` / `SystemNotification` into Minecraft commands.

The SDK owns transport, request waiters, task lifetimes, ordering and fragment
state. The host owns authorization, history and business state. There is no
SDK-owned `PlayerSession`; isolate application state by `(connection_id, sender)`
using the current `PlayerMessageEvent.sender`.

## Managed dispatch

Setup sends handshake and subscribes to `PlayerMessage` before delivering the
connected notification. The welcome banner remains host-owned.
The reader classifies and correlates command replies, negotiation and reserved
addon traffic before queuing business callbacks. Awaiting a command/capability
inside a managed callback leaves the reader free to receive its reply.

Same-player chat stays FIFO across interceptors and the hook; different players
can progress concurrently. Other native business events share a FIFO lane.
Observers use a separate bounded lane. Business/observation overload rejects
work explicitly. Callback exceptions are isolated; disconnect cancels and joins
managed work, fails waiters and releases buffers. This differs from the legacy
reader's inline callback ordering; see [migration](migration.md).

Hooks return `None`. `on_player_message(state, event, parsed=None)` receives an
optional registry match, not a consumed flag. UI chat uses
`on_ui_chat_reassembled(state, UiChatMessage)`; optional `AddonControlHook`
receives session/approval DTOs. The old three-argument UI callback requires the
explicit `LegacyUiChatHookAdapter`.

## Delivery and protocols

The response sender wraps queued messages with `RouteEnvelope.from_message()`
and calls the sink's two `on_*` methods. Hosts can use `McbeOutboundDelivery`
with `state.send_bulk` to classify long output separately from requests.
The bounded sender reserves control capacity and schedules bulk fairly.

The final commandLine budget defaults to 461 UTF-8 bytes and may only be
lowered. Tellraw and framed ScriptEvents can be split; native commands remain
atomic and reject overflow. V2 uplink uses its own carrier bounds.

`McbewsV1Profile` / `MCBEWS_V1` define the legacy profile, not a replaceable
runtime seam. Installed manifests and vectors define v1 and v2 separately.
`V2Bridge` negotiates per connection; it falls back only to known v1 methods.
Required metadata and streams fail when unsupported. Reconnect creates a fresh
session without replaying requests. See [v1](addon-bridge-protocol.md) and
[v2](protocol-v2.md) for authoritative formats and resource limits.

## Addon requirements and trust

The companion addon needs **Experiments → Beta APIs** in the target world.
Without it scripts do not load and capability requests time out. See the
[addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md).
The bridge and metadata identity are not authentication boundaries. Hosts must
authenticate and authorize callers. Automated protocol coverage does not expand
live-game platform/version support.

The SDK imports no host application modules. `ConnectionState.send_payload`
is an opaque async callable wired to transport by the facade; host-specific
message IDs, timestamps and business framing stay outside the SDK.

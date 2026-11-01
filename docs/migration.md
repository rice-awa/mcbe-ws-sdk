# Migrating to the MCBEWS/2-only release

This is a breaking release. The SDK no longer speaks MCBEWS/1 and no longer
offers a second server entry point. Deploy the Python package and the companion
addon as one version; mixed old/new deployments are rejected by design.

The symbols and routes marked **removed** below no longer exist in this version.
They are named here only so existing hosts can find what to replace.

## What changed at a glance

| Area | Old dual-protocol release | This release |
| --- | --- | --- |
| Python entry | `McbeServerFacade` plus `McbeServer` | `McbeServer` only |
| Dispatch | `legacy` callback order or `reliable` lanes | Always reliable managed lanes |
| Protocol | MCBEWS/1 by default, MCBEWS/2 opt-in | MCBEWS/2 required for every connection |
| Admission | Business could run before discovery finished | `READY_V2` required before business |
| Fallback | Old addons continued on known v1 methods | No fallback; old peers are rejected |
| Addon routes | `mcbews:bridge_req`, `mcbews:text_resp`, UI/session routes | `mcbews:negotiate_v2`, `mcbews:packet_v2` only |
| Host business DTOs | SDK-owned UI chat, session, approval, text response | Removed; model these as host V2 methods |
| Streams | Negotiated `stream/1` on the v2 path | `stream/1` on the only path |

## Admission is mandatory

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

- `WS_TRANSPORT_CONNECTED` is transport only. It is not business readiness.
- `NEGOTIATING` handles only negotiation, required control frames and correlated
  native `commandResponse` / `error` frames. Business frames are dropped, not
  buffered for replay.
- `on_connected` fires once at `READY_V2`. A rejected connection never emits a
  business `on_connected` or a paired business `on_disconnected`.
- Failures close the WebSocket with code `1008` and a bounded reason token.

| Peer combination | Result | Business side effects |
| --- | --- | --- |
| New server + new addon | One MCBEWS/2 admission; ready | Allowed after `READY_V2` |
| New server + old addon | `UNSUPPORTED_PROTOCOL` or `NEGOTIATION_TIMEOUT`, then close | None |
| New server + no addon | Bounded timeout, then close | None; `!wscmd` does not run |
| Old server + new addon | Addon logs and rejects old events | None; the addon cannot close a WebSocket it does not own |
| Any mixed old/new business frame after ready | Rejected | None |

Typical rejection reasons: `UNSUPPORTED_PROTOCOL`, `NEGOTIATION_TIMEOUT`,
`NEGOTIATION_CONFIRMATION_TIMEOUT`, `NEGOTIATION_BUSY`, `INVALID_NEGOTIATION`,
`SESSION_INVALID`.

The native Bedrock WebSocket field `header.version = 1` is part of the game
protocol and is unrelated to MCBEWS/1. It stays as Bedrock defines it.

## Removed Python API

### Entry points and dispatch

| Removed | Replacement |
| --- | --- |
| `McbeServerFacade` | `McbeServer` |
| `DispatchSettings(mode="legacy" \| "reliable")` switch | Removed. `McbeServer` always uses managed lanes |
| `ConnectionHook.on_ui_chat_reassembled` | Removed. Use native events or host V2 methods |
| `AddonControlHook` | Removed |
| `LegacyUiChatHook`, `LegacyUiChatHookAdapter` | Removed |
| `V2Bridge(negotiate=False)` | Removed. Admission is automatic and mandatory |
| `V2Bridge.activate(...)` as a public manual path | Removed. Endpoints are installed by admission |

`ConnectionHook` now has five lifecycle hooks: `on_connected`,
`on_disconnected`, `on_player_message`, `on_command_response`, `on_error`.
`NoOpHook` implements all five.

### Legacy protocol surface

| Removed | Notes |
| --- | --- |
| `mcbe_ws_sdk.profiles.mcbews_v1.*` | No v1 profile, manifest, vectors, codecs or delivery |
| `MCBEWS_V1`, `McbewsV1Profile`, `McbewsV1Protocol` | Removed |
| `McbewsV1Delivery` | Use `McbeOutboundDelivery` / `V2Endpoint` |
| `mcbe_ws_sdk.extensions.mcbews_v1.*` | Removed |
| v1 bridge codecs and wire vectors | Old frames are rejected, not decoded for business |

The 461-byte empirical command budget was preserved, but it now comes from the
MCBEWS/2 manifest (`limits.command_bytes`). It may only be lowered.

### Addon bridge and host business helpers

| Removed | Replacement |
| --- | --- |
| `AddonBridgeService` | `server.v2` (`V2Bridge`) |
| `AddonBridgeClient`, `ConnectionAddonBridgeClient` | `V2Bridge.request` / `V2Bridge.stream` |
| `AddonBridgeSettings`, `AddonMessageResult`, `GatewaySettings.addon` | Removed; transport settings live in `GatewaySettings` |
| `UiChatMessage`, `ToolPlayerMessage` | Removed |
| `SessionRequest`, `SessionResponse`, `ApprovalDecision`, `SessionError` | Removed |
| `TextResponseChunk`, `TextResponseMessage`, `TokenUsage`, text-response codecs | Removed |
| `mcbews:bridge_req`, `mcbews:session_req`, `mcbews:text_resp` | Removed; rejected with zero side effects |

If your host still needs session, approval, UI-chat or text-response semantics,
implement them as ordinary MCBEWS/2 methods and notifications in your own
capability registry. The SDK no longer ships business DTOs, channels or
aggregate handlers for them.

## Removed addon API and routes

| Removed | Replacement |
| --- | --- |
| `setCapabilityHandler` aggregate entry | `registerCapability(name, handler, options)` |
| `mcbews:bridge_req` parser / response sender | `mcbews:negotiate_v2` + `mcbews:packet_v2` carrier |
| `preReadyQueue` business backlog | Removed; business before readiness is dropped |
| `activateV2Bridge` public manual activation | Removed; activation happens after world load |
| `mcbews:text_resp` subscription | Removed |
| `responseSync.ts` / `extensions/textResponse.ts` | Removed |
| `chunkBridgePayload`, `chunkUiChatPayload` | Removed; `utf8ByteLength` and neutral limits remain |
| `MCBEWS\|BRIDGE`, `MCBEWS\|UI_CHAT`, `MCBEWS\|SESSION`, `MCBEWS\|TOOL_APPROVE` | Removed; no longer emitted or accepted |

`getV2Endpoint(epoch)`, `registerCapability`, `unregisterCapability`,
`registerV2Stream` and the read-only capability registry remain. The transport
still uses the simulated player `MCBEWS_BRIDGE` for uplink frames; that name is
transport attribution, not authentication.

## Migration recipes

### 1. Replace the entry point

```python
# Old
server = McbeServerFacade(hook=hook)

# New
server = McbeServer(hook=hook)
```

Do not add a background task around hooks. Managed lanes keep the reader free
while a hook awaits a command or capability reply.

### 2. Replace capability calls

```python
# Old
service = AddonBridgeService(AddonBridgeSettings())
response = await service.request(state, "get_player_snapshot", params)

# New
response = await server.v2.request(
    state.id, "get_player_snapshot", {"target": player_name}
)
```

The new call returns the response envelope; successful data is under `result`.
Remote protocol failures raise `RemoteCallError` with `code`. There is no v1
result wrapper.

### 3. Replace capability registration

Python:

```python
registration = server.v2.registry.register("demo.echo", handler, validate=validate)
```

Addon:

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("demo.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
```

Duplicate names require `replace: true`. Unregistering affects future calls
only.

### 4. Replace UI / session / approval / text-response flows

Model each flow as a V2 method or notification:

- a UI submission is a request from the addon to a Python handler;
- an approval decision is a request or notification with your own stable method
  name and schema;
- streamed text is a `stream/1` producer/consumer, not a chunked
  `mcbews:text_resp` frame;
- player-facing rendering remains your `ResponseSink` responsibility.

The SDK no longer interprets these payloads or exposes typed DTOs for them.

### 5. Rebuild the addon

The old build scripts still produce a pack, but it will not admit. Rebuild from
the new sources and re-import the pack:

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

Demo capabilities ship in the default package. Use `npm run build:base` /
`npm run mcaddon:base` only when you want the lean two-snapshot pack.

## Rollback and deployment

This release has no v1 fallback. A rollback means redeploying a complete older
version of **both** the Python package and the addon, then reconnecting.

- Mixed versions do not negotiate: the new server rejects the old addon and the
  new addon rejects the old server's business frames.
- A reconnect creates a new epoch and session. Old requests, streams, approvals
  and reassembly state are not restored.
- Already executed world commands are not undone, and side effects are not
  replayed automatically.
- If a rollback needs v1 behavior, it must restore the old Python package and
  old addon assets together; this version cannot be configured into v1 mode.

## What did not change

- `PlayerMessageEvent.sender` is still the authoritative player identity.
- `NoOpHook`, `ConnectionHook`, `ResponseSink`, `OutboundText`,
  `SystemNotification` and `McbeOutboundDelivery` keep their roles.
- Native event subscriptions and interceptors keep their contract.
- The 461-byte command budget, sentence-aware splitting and native-command
  atomicity are unchanged.
- Automated tests do not prove live-game support; verify the target game
  version, platform and addon loading separately.

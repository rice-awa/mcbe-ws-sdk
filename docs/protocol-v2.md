# MCBEWS/2 protocol (required)

MCBEWS/2 is the SDK's only wire protocol. Every WebSocket connection must
complete admission before business traffic runs. There is no negotiation
opt-out, no MCBEWS/1 fallback and no pre-ready business buffering.

The installed `profiles/mcbews_v2/manifest.json` and `vectors.json` are
authoritative for identifiers, field rules, errors, limits and negotiation
frames. This page explains the contract; it does not override the manifest.

MCBEWS/2 borrows the request/response shape of JSON-RPC but is not
JSON-RPC-compatible.

## Admission

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

| State | Behaviour |
| --- | --- |
| `WS_TRANSPORT_CONNECTED` | Transport only. No business readiness. |
| `NEGOTIATING` | Accept negotiation, required control frames and correlated native `commandResponse` / `error`. Business frames are dropped, not buffered. |
| `READY_V2` | Publish business `CONNECTED`, call `on_connected`, route player messages and carrier frames. |
| `REJECTED` | Record the reason, send at most a bounded best-effort hint, then close. |
| `CLOSING` / `CLOSED` | Release requests, queues, tasks, leases and fragments. |

Admission failures close the WebSocket with code `1008`. The close reason is a
bounded token of at most 123 UTF-8 bytes. Stable reasons:

| Reason | Meaning |
| --- | --- |
| `UNSUPPORTED_PROTOCOL` | The peer spoke a retired protocol or an unsupported version |
| `NEGOTIATION_TIMEOUT` | No addon, no reply, or the scripts did not load |
| `NEGOTIATION_CONFIRMATION_TIMEOUT` | The challenge was answered but confirmation did not finish |
| `NEGOTIATION_BUSY` | Admission capacity or the provisional-challenge limit is full |
| `INVALID_NEGOTIATION` | Malformed, mismatched or expired control frames |
| `SESSION_INVALID` | An admitted session was revoked or became stale |

A rejected connection never emits a business `on_connected` or a paired
business `on_disconnected`. Reconnect starts a new attempt and a new epoch; a
late frame from a closed attempt cannot reopen admission.

### Control frames

The addon accepts exactly two script event ids: `mcbews:negotiate_v2` and
`mcbews:packet_v2`.

```text
Python → addon   MCBEWS2H|attempt
addon → Python   MCBEWS2N|attempt|epoch|2|comma-separated-features
Python → addon   MCBEWS2C|attempt|epoch
addon → Python   MCBEWS2N|attempt|epoch|ready
addon → Python   MCBEWS2N|attempt|-|busy
Python → addon   MCBEWS2R|epoch          (lease refresh)
addon → Python   MCBEWS2N|epoch|lease
Python → addon   MCBEWS2X|epoch          (close)
```

The `attempt` and `epoch` tokens are fresh 1–24 character ASCII routing
tokens. An epoch identifies a session; it is not authentication. One
outstanding attempt is admitted per connection, and the challenge must match
before activation. A busy or expired attempt terminates; retries are bounded.

### Retired routes

`mcbews:bridge_req`, `mcbews:session_req` and `mcbews:text_resp` are retired.
The addon logs and rejects them with zero business side effects: nothing is
parsed, queued, answered or dispatched. `MCBEWS|BRIDGE`, `MCBEWS|UI_CHAT`,
`MCBEWS|SESSION` and `MCBEWS|TOOL_APPROVE` are not produced or accepted.

The addon cannot close a WebSocket owned by an old server. It only refuses
that server's business frames. A new server rejects an old addon or a missing
addon by timeout and closes the transport it owns.

The native Bedrock WebSocket field `header.version = 1` belongs to the game
protocol and is unrelated to MCBEWS/1. Do not use it to identify a retired
client.

## Logical messages

Every message is a strict JSON object:

```json
{"v": "MCBEWS/2", "kind": "request", "id": "p.session.1", "method": "demo.echo", "params": {}}
```

- `v` is exactly `MCBEWS/2`.
- `kind` is `request`, `response`, `notification`, `stream_open`,
  `stream_item`, `stream_end`, `stream_error` or `stream_cancel`.
- Each kind defines required, optional and forbidden fields. Unknown core keys
  are rejected.
- `method` matches the manifest's ASCII pattern.
- `params` and `result` accept any JSON value, including `null`.
- A response contains exactly one of `result` or `error`.
- An error contains a listed `code` and a scalar `message` of at most 256
  UTF-8 bytes, with optional JSON `data`. Unknown error fields are rejected.

### IDs

Logical IDs use `origin.session.sequence`:

- `p` means Python originated the request; `a` means the addon did.
- `session` is a fresh 1–24 character ASCII token per connection epoch.
- `sequence` is a positive decimal with 1–10 digits.

Responses echo the request ID. Opposite origins cannot collide. Notifications
have no logical ID and no reply waiter. Every carrier message, including
replies and notifications, uses a fresh transport ID owned by its actual
sender, so a reply's logical origin may differ from the transport sender.
Never replay messages or restore IDs after reconnect.

### JSON rules

- Numbers must be finite safe integers within ±9007199254740991; booleans stay
  booleans. Scientific notation for a safe integer is accepted.
- Numeric tokens are checked after IEEE-754 parsing, as `JSON.parse` does:
  `1.0000000000000001` becomes `1`, `1e-4000` becomes `0`. Oversized integer
  tokens are `INVALID_MESSAGE`, not malformed JSON.
- Strings must contain Unicode scalar values; lone surrogates are rejected.
- Duplicate object keys use the last value.
- Root depth is 0; maximum depth is 16.
- The full compact JSON message is at most 65536 UTF-8 bytes.

### Metadata

`meta` is an object of at most 4096 compact JSON UTF-8 bytes. Unknown optional
keys and nested values are preserved. The `sdk.` prefix is reserved:

| Key | Rule |
| --- | --- |
| `sdk.trace_id` | A manifest token |
| `sdk.identity` | Untrusted identity object; never authorizes |
| `sdk.capabilities` | Unique list of at most 16 manifest tokens; `stream/1` enables streams |

Unknown reserved keys are rejected. The same depth and number rules apply to
metadata. Callers cannot set reserved keys; the dedicated API argument supplies
the trace ID.

## Bidirectional carrier and resources

Downlink uses `scriptevent mcbews:packet_v2 FRAME`. Uplink uses chat content
`FRAME` sent by the simulated player `MCBEWS_BRIDGE`:

```text
MCBEWS2|sender.session.sequence|index/total|content
```

- `MCBEWS_BRIDGE` is transport attribution, not authentication.
- Split only at Unicode scalar boundaries. Content may contain pipes and
  newlines.
- `index` and `total` are canonical positive decimals, 1-based, at most 1024.
- All indexes must arrive before decoding. Identical duplicates are ignored
  without refreshing the TTL. Conflicting duplicates or a changed total
  release the message and reject that transport ID.
- Each connection/source/ID completes once during its tombstone lifetime.

Manifest limits are hard maxima and may only be lowered:

| Scope | Default |
| --- | --- |
| Downlink final `commandLine` | 461 UTF-8 bytes |
| Uplink full carrier frame | 256 Unicode code points and 1024 UTF-8 bytes |
| One logical message | 65536 bytes |
| Incomplete messages per source | 16 IDs / 131072 bytes |
| Incomplete messages per connection | 64 IDs / 262144 bytes |
| Process/world shared pool | 256 IDs / 1048576 bytes |
| Active IDs plus tombstones | 2048 |

The downlink budget is empirical; deployments may lower it. Uplink measures the
whole frame independently and does not inherit the 461-byte budget.

Idle TTL is 30000 ms. Each receive samples the millisecond clock once; the exact
boundary expires. Completion, conflict, capacity rejection, expiry and
cancellation release bytes and retain a tombstone for 30000 ms. When active IDs
plus tombstones reach the limit, new IDs are rejected without evicting
tombstones. Disconnect clears that connection's buffers and tombstones.

Missing fragments never deliver partial messages. This is bounded duplicate
suppression, not retransmission or exactly-once execution. Reassembly never
invokes handlers. Native commands remain atomic and reject oversized commands.

## Encoding decision

Object JSON was chosen over an eight-position array after comparing identical
samples with the same worst-case carrier header and final command-byte
accounting. Arrays saved no commands in those samples, require a presence mask
to distinguish absent fields from `null`, and complicate validation. The
comparison is deterministic byte cost, not latency or game measurement.

Run `python tools/compare_v2_formats.py` for the current table.

## Integration points

Python:

```python
server = McbeServer()
bridge = server.v2

response = await bridge.request(connection_id, "demo.echo", {"text": "hi"})
await bridge.notify(connection_id, "demo.event", {"value": 1})
stream = await bridge.stream(connection_id, "demo.ticks", {"count": 3})
```

`bridge.ready(connection_id)` waits for admission and returns the negotiated
connection (version, features, endpoint). Hooks already run after `READY_V2`, so
they normally do not need it.

Lower-level pieces remain available: `profiles.mcbews_v2.codec` for
encode/decode, `transport` for fragmentation, downstream payloads and
`ReassemblyPool`. The addon counterparts live in `bridge/v2/codec` and
`bridge/v2/transport`. Send loops await one scheduled frame at a time, preserve
order and stop on failure or cancellation.

The addon router exposes `getV2Endpoint(epoch)`, `registerCapability` and
`registerV2Stream`. A method's ordering group cannot be re-entered; new calls
during an active group are rejected with `LIMIT_EXCEEDED`, while reverse calls
to other groups are allowed. Notifications receive no error reply.

For Python requests, `V2Bridge(timeout_seconds=...)` sets one deadline covering
scheduler admission, fragment sending and the reply wait. Discovery has its own
timeout. Timeout, close and cancellation stop unsent fragments; a frame already
handed to the transport may still complete and cannot be retracted.

Handlers receive transport attribution separately from metadata. Metadata
identity is untrusted. Disconnect fails waiters and releases the connection's
fragments; idle partial messages expire by TTL.

## Request streams

Python registers an async generator with `bridge.streams.register(name, handler)`.
The handler receives `(name, params, CapabilityContext)` and yields JSON items.

```python
stream = await server.v2.stream(state.id, "demo.ticks", {"count": 3})
async with stream:
    async for item in stream:
        print(item)
```

The addon registers producers with `registerV2Stream(name, handler)`; handlers
return an `AsyncIterable`. `await endpoint.stream(name, params, options)`
returns an async iterator; early exit from `for await` invokes `return()` and
cancellation. Registration removal affects future calls only.

| Kind | Required fields beyond `v` / `kind` | Direction |
| --- | --- | --- |
| `stream_open` | `id`, `method`, `params` | Caller → producer |
| `stream_item` | `id`, `seq`, `item` | Producer → caller |
| `stream_end` | `id`, `seq` | Producer → caller |
| `stream_error` | `id`, `seq`, `error` | Producer → caller |
| `stream_cancel` | `id` | Caller → producer |

All kinds allow optional `meta` and reject other fields. IDs belong to the
caller; responses echo that ID. Each logical item has a fresh sender-owned
transport ID and may use multiple fragments. Item sequence numbers are separate
from fragment indexes: the first item is `seq=0`, and end/error carries the next
expected sequence. The total length is unknown until termination. An empty
stream ends at `seq=0`.

A sequence mismatch fails with `INVALID_MESSAGE` and cancels the producer.
Unknown or late messages allocate no stream. Duplicate opens cannot restart a
producer within the tombstone window; if the tombstone table is full, new opens
are rejected without storing another ID.

Normal completion preserves already buffered items until consumed. Failure,
local cancellation and overload discard buffered items and settle the consumer
once. Producer exceptions map to `HANDLER_FAILED`; inactivity maps to
`TIMEOUT`. Cancellation stops future items and closes the producer iterator; it
never rolls back world changes.

| Stream resource | Default |
| --- | --- |
| Active streams per endpoint | 16 both directions, including completed streams with unread items |
| Buffered items per consumer | 32 items / 131072 item JSON UTF-8 bytes |
| Buffered bytes per endpoint | 262144 |
| Active producers plus duplicate-open tombstones | 2048; tombstones last 30000 ms |
| Idle deadline | 30000 ms each for open, next item and each message |

A producer sends one logical item at a time; receiving items refreshes the
consumer idle timer. This is not a total stream lifetime limit. Limits can only
be lowered through Python `V2Bridge(stream_limits=...)` or addon
`V2EndpointOptions.streamLimits`.

Slow consumers fail explicitly with `LIMIT_EXCEEDED` and receive a
cancellation. Fixed send intervals and transport pacing do not provide receiver
backpressure. Control sends are bounded; when their capacity or the transport
is exhausted, remote idle expiry still bounds the lifetime. Incomplete transport
messages retain the general TTL and quotas; logical stream cancellation does not
require decoding incomplete fragments. Disconnect clears the connection pool.

## Trust boundary

Carrier source type and routing tokens do not prove WebSocket identity or
authorize a caller. Hosts must authenticate and authorize independently. A lost
close relies on lease expiry; a command already handed to the transport cannot
be retracted.

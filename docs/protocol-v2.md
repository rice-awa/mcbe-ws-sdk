# MCBEWS/2 protocol

The current SDK and companion addon use **V2 (MCBEWS/2)**. A connection
completes protocol negotiation before any business message is processed.
Business frames that arrive before the connection is ready are dropped, not
buffered for replay.

The installed `profiles/mcbews_v2/manifest.json` and `vectors.json` are
authoritative for identifiers, field rules, errors, limits and negotiation
frames. This page explains the protocol; it does not override the manifest.

MCBEWS/2 borrows the request/response shape of JSON-RPC but is not
JSON-RPC-compatible.

Three terms come up repeatedly below:

- An **epoch** identifies one negotiated session. A reconnect always gets a new one.
- A **carrier** frame is the text that actually travels through Minecraft and holds
  one fragment of a logical message: a script event going down, a chat message
  coming up.
- A **tombstone** is a short record kept for 30000 ms after a transport ID is
  finished or rejected, so the receiver can recognize and ignore late duplicates.

## Connection setup

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

| State | Behaviour |
| --- | --- |
| `WS_TRANSPORT_CONNECTED` | The transport is open, but the connection is not ready for business yet. |
| `NEGOTIATING` | Only negotiation frames, required control frames and correlated native `commandResponse` / `error` frames are accepted. Business frames are dropped, not buffered. |
| `READY_V2` | The SDK publishes business `CONNECTED`, calls `on_connected`, and routes player messages and carrier frames. |
| `REJECTED` | The SDK records the reason, sends at most one short best-effort hint, then closes. |
| `CLOSING` / `CLOSED` | Requests, queues, tasks, leases and fragments are released. |

When negotiation fails, the WebSocket closes with code `1008`. The close reason
is a short token of at most 123 UTF-8 bytes. The reasons are stable:

| Reason | Meaning |
| --- | --- |
| `UNSUPPORTED_PROTOCOL` | The peer spoke a retired protocol or an unsupported version |
| `NEGOTIATION_TIMEOUT` | No addon, no reply, or the scripts did not load |
| `NEGOTIATION_CONFIRMATION_TIMEOUT` | The challenge was answered but confirmation did not finish |
| `NEGOTIATION_BUSY` | Negotiation capacity or the provisional-challenge limit is full |
| `INVALID_NEGOTIATION` | Malformed, mismatched or expired control frames |
| `SESSION_INVALID` | An active session was revoked or became stale |

A rejected connection never emits a business `on_connected`, so it never emits a
matching business `on_disconnected` either. Reconnecting starts a new attempt
with a new epoch, and a late frame from a closed attempt cannot reopen the
connection.

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

The `attempt` and `epoch` values are freshly generated routing tokens of 1–24
ASCII characters. An epoch identifies a session but is not authentication. Each
connection may have only one unfinished attempt at a time, and the challenge
must match before the session is activated. A busy or expired attempt ends, and
the number of retries is limited.

### Retired routes

`mcbews:bridge_req`, `mcbews:session_req` and `mcbews:text_resp` are retired.
The addon logs and rejects them without triggering any business logic: nothing
is parsed, queued, answered or dispatched. `MCBEWS|BRIDGE`, `MCBEWS|UI_CHAT`,
`MCBEWS|SESSION` and `MCBEWS|TOOL_APPROVE` are neither produced nor accepted.

The addon cannot close a WebSocket that an old server owns; it can only refuse
that server's business frames. A new server, on the other hand, rejects an old
or missing addon by timing out, and it closes the transport it owns.

The native Bedrock WebSocket field `header.version = 1` belongs to the game's
own protocol and is unrelated to MCBEWS/1. Do not use it to identify a retired
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

A response echoes the ID of its request. Because the origin is part of the ID,
requests that both sides start at the same moment cannot collide. Notifications
have no logical ID and create no reply waiter. Every carrier message, including
replies and notifications, uses a fresh transport ID owned by the side that
actually sends it, so the logical origin of a reply may differ from its
transport sender. Never replay messages or restore IDs after a reconnect.

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

Unknown reserved keys are rejected, and metadata follows the same depth and
number rules as messages. Callers cannot set reserved keys themselves; the trace
ID is passed through a dedicated API argument.

## Bidirectional carrier and resources

Downlink frames are sent with `scriptevent mcbews:packet_v2 FRAME`. Uplink
frames are sent as chat content `FRAME` by the simulated player `MCBEWS_BRIDGE`:

```text
MCBEWS2|sender.session.sequence|index/total|content
```

- `MCBEWS_BRIDGE` is transport attribution, not authentication.
- Before fragmentation, encode every literal `@` in the JSON as `\u0040` so
  Minecraft's `scriptevent` and `tell` message parsers cannot expand it into a
  selector. The receiver reassembles the fragments first and decodes the JSON
  afterwards, which restores the original strings. The escaped bytes count
  toward the message, fragment and buffer limits.
- Split only at Unicode scalar boundaries. Content may contain pipes and
  newlines.
- `index` and `total` are canonical positive decimals. Counting starts at 1, and
  a message has at most 1024 fragments.
- Every index must arrive before the message is decoded. An identical duplicate
  fragment is ignored and does not refresh the idle TTL. A conflicting
  duplicate, or a change in the total, releases the message and rejects that
  transport ID.
- A given connection, source and ID completes only once while its tombstone is
  retained.

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

The downlink budget was determined empirically, and deployments may lower it.
Uplink measures the whole frame on its own and does not inherit the 461-byte
budget.

The idle TTL is 30000 ms. Each receive samples the millisecond clock once, and a
message that is exactly at the deadline counts as expired. Completion, conflict,
capacity rejection, expiry and cancellation all release the buffered bytes and
leave a tombstone for 30000 ms. Once active IDs plus tombstones reach the limit,
new IDs are rejected, and existing tombstones are not evicted to make room. A
disconnect clears that connection's buffers and tombstones.

A message with missing fragments is never delivered in part. What this layer
provides is duplicate suppression within fixed limits, not retransmission or
exactly-once execution. Reassembly never invokes handlers. Native commands are
never split, and an oversized one is rejected.

## Encoding decision

The protocol uses object JSON rather than an eight-position array. The choice
came from comparing identical samples under the same worst-case carrier header
and the same final command-byte accounting. In those samples arrays saved no
commands, needed a presence mask to tell an absent field from `null`, and made
validation harder. The comparison measures deterministic byte cost; it says
nothing about latency or in-game performance.

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

`bridge.ready(connection_id)` waits for negotiation to finish and returns the
negotiated connection (version, features and endpoint). Hooks already run after
`READY_V2`, so they normally do not need it.

The lower-level pieces remain available: `profiles.mcbews_v2.codec` for
encoding and decoding, and `transport` for fragmentation, downstream payloads
and `ReassemblyPool`. The addon counterparts are `bridge/v2/codec` and
`bridge/v2/transport`. A send loop waits for one scheduled frame at a time,
keeps the order, and stops on failure or cancellation.

The addon router exposes `getV2Endpoint(epoch)`, `registerCapability` and
`registerV2Stream`. The ordering group of a method cannot be re-entered: while a
group is active, new calls into it are rejected with `LIMIT_EXCEEDED`, but
reverse calls into other groups are still allowed. A notification never gets an
error reply.

For Python requests, `V2Bridge(timeout_seconds=...)` sets a single deadline that
covers acceptance into the scheduler queue, sending the fragments and waiting
for the reply. Negotiation has its own separate timeout. A timeout, close or
cancellation stops fragments that have not been sent yet, but a frame already
handed to the transport may still be delivered and cannot be retracted.

Handlers receive the transport attribution separately from the metadata, and the
identity in metadata is untrusted. A disconnect fails all pending waiters and
releases the connection's fragments, and incomplete messages that sit idle
expire after the TTL.

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
returns an async iterator, and leaving a `for await` loop early calls `return()`
and cancels the stream. Removing a registration affects future calls only.

| Kind | Required fields beyond `v` / `kind` | Direction |
| --- | --- | --- |
| `stream_open` | `id`, `method`, `params` | Caller → producer |
| `stream_item` | `id`, `seq`, `item` | Producer → caller |
| `stream_end` | `id`, `seq` | Producer → caller |
| `stream_error` | `id`, `seq`, `error` | Producer → caller |
| `stream_cancel` | `id` | Caller → producer |

Every kind allows an optional `meta` and rejects any other field. The ID belongs
to the caller, and responses echo it. Each logical item gets a fresh transport ID
owned by the sender and may span several fragments. Item sequence numbers are
independent of fragment indexes: the first item is `seq=0`, and an end or error
message carries the next expected sequence. The total length is not known until
the stream terminates, and an empty stream ends at `seq=0`.

A sequence mismatch fails the stream with `INVALID_MESSAGE` and cancels the
producer. An unknown or late message never creates a stream. A duplicate open
cannot restart a producer while its tombstone is retained, and if the tombstone
table is full, new opens are rejected without storing another ID.

On normal completion, items that are already buffered stay available until the
consumer reads them. Failure, local cancellation and overload discard the
buffered items, and the consumer receives its final result exactly once. A
producer exception maps to `HANDLER_FAILED`, and inactivity maps to `TIMEOUT`.
Cancellation stops further items and closes the producer's iterator; it never
rolls back world changes.

| Stream resource | Default |
| --- | --- |
| Active streams per endpoint | 16 both directions, including completed streams with unread items |
| Buffered items per consumer | 32 items / 131072 item JSON UTF-8 bytes |
| Buffered bytes per endpoint | 262144 |
| Active producers plus duplicate-open tombstones | 2048; tombstones last 30000 ms |
| Idle deadline | 30000 ms each for open, next item and each message |

A producer sends one logical item at a time, and each item the consumer receives
refreshes its idle timer, so the idle deadline is not a limit on the total
lifetime of a stream. The limits can only be lowered, through
`V2Bridge(stream_limits=...)` in Python or `V2EndpointOptions.streamLimits` in
the addon.

A slow consumer fails explicitly with `LIMIT_EXCEEDED`, and a cancellation is
sent. Fixed send intervals and transport pacing are not receiver backpressure.
Control sends have limited capacity too; when that capacity or the transport is
exhausted, the remote side's idle expiry still bounds the stream's lifetime.
Incomplete transport messages remain subject to the general TTL and quotas, and
cancelling a logical stream does not require decoding incomplete fragments. A
disconnect clears the connection's pool.

## Trust boundary

The carrier's source type and its routing tokens neither prove the identity of
the WebSocket peer nor authorize a caller, so hosts must authenticate and
authorize on their own. If a close message is lost, cleanup relies on the lease
expiring, and a command that has already been handed to the transport cannot be
retracted.

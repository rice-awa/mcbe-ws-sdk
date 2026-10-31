# MCBEWS/2 contract (opt-in)

Installed profiles/mcbews_v2/manifest.json and vectors.json are authoritative.
A separate interaction protocol inspired by JSON-RPC, this is not JSON-RPC compatible.
MCBEWS/1 assets, identifiers, imports, constructors and default routing stay unchanged.
The v2 runtime is opt-in; request streams require negotiated `stream/1`.

## Logical messages

Strict JSON object fields follow the manifest: v is exactly MCBEWS/2; kind is request,
response, notification, or one of the stream kinds below. Each kind lists required, optional and forbidden fields;
unknown core keys are rejected. Method matches the ASCII manifest pattern. Params and
result accept any JSON value including null. A response contains exactly one result or
error. Error requires a listed code and scalar message (at most 256 UTF-8 bytes), with
optional JSON data; unknown error fields are rejected.

IDs use origin.session.sequence: p originates at Python, a at the addon; session is a
fresh 1–24 character ASCII token per connection epoch, sequence is a positive 1–10 digit
decimal. Responses echo the request ID. Opposite origins cannot collide. Notifications
have no logical ID or reply waiter. Every carrier message, including replies and
notifications, uses a fresh transport ID owned by its actual sender. A reply may thus
have a different logical origin. Never replay messages or restore IDs after reconnect.

Numbers must be finite safe integers in ±9007199254740991; booleans remain booleans.
Scientific notation representing a safe integer is accepted.
Numeric tokens are checked after IEEE-754 parsing, as in JSON.parse: for example,
1.0000000000000001 becomes 1 and 1e-4000 becomes 0. Oversized integer tokens
are INVALID_MESSAGE, not malformed JSON; no process-wide parser limits are disabled.
Strings must contain Unicode scalar values, never lone surrogates. Duplicate JSON object keys use the last
value, matching both native parsers. Root depth is zero, maximum depth is 16. The full
compact JSON message is at most 65536 UTF-8 bytes.

Meta is an object, at most 4096 compact JSON UTF-8 bytes. Unknown optional keys and
nested values are preserved. The sdk. prefix is reserved: sdk.trace_id is a manifest
token; sdk.identity is an untrusted object; sdk.capabilities is a unique list of at most
16 manifest tokens. `stream/1` enables the stream kinds described below. Identity never authorizes a caller. Unknown reserved keys are rejected.
The same depth and number rules apply to metadata.

## Bidirectional carrier and resources

Downlink: scriptevent mcbews:packet_v2 FRAME. Uplink chat content: FRAME.
FRAME is MCBEWS2|sender.session.sequence|index/total|content. Split only at Unicode
scalar boundaries. Content can include pipes and newlines. Index and total are canonical
positive decimal integers, 1-based, at most 1024. All indexes must arrive before decode.
Identical duplicates are ignored without refreshing TTL. Conflicting duplicates or total
changes release the message and reject that transport ID. Each connection/source/ID
completes once during its tombstone lifetime.

Manifest limits are hard maxima and may only be lowered. Lowered metadata_bytes and
depth limits apply before sending and after reassembly on receive. Downlink measures final
commandLine UTF-8 bytes, default 461, never raiseable. Uplink independently measures the
whole FRAME: 256 Unicode code points and 1024 UTF-8 bytes; it does not inherit 461.
Each message is at most 65536 bytes. Incomplete buffers share quotas per source
(16 IDs / 131072 bytes), connection (64 / 262144), and process/world pool (256 / 1048576).
One pool must serve all connections in the runtime. Source is transport attribution,
never metadata identity.

Idle TTL is 30000 ms. Each receive uses one millisecond clock sample; the exact
idle TTL boundary expires.
An over-budget or non-scalar frame with a valid prefix and sender transport ID
also releases its active transfer and tombstones it; unattributable input allocates nothing.
Completion, conflict, capacity rejection, expiry and cancellation
release bytes and retain a tombstone for 30000 ms. Active IDs plus tombstones are limited
to 2048; when full reject new IDs without evicting tombstones. Disconnect clears that
connection's buffers and tombstones; the caller must reject old epochs. Prune on receive
and periodic lifecycle tick; explicit cancel/close release immediately. Missing fragments
never deliver partial messages. This is bounded duplicate suppression, not retransmission
or exactly-once execution. Reassembly never invokes handlers. Native commands remain
atomic and reject oversized commands.

## Encoding decision and compatibility

Run python tools/compare_v2_formats.py. Seven identical samples compare object JSON
with an eight-position JSON array, using the same worst-case carrier header and final
command byte accounting: Unicode request 292/261; null response 151/146; reverse request
205/186; notification 238/217; error 217/213; max ID 250/231; 4096-byte metadata
5189/5168 over 12 commands each. Other samples use one command each. Choose object JSON:
arrays save no commands here, require a presence mask to distinguish absent fields from
null, and complicate validation/extensions. These are deterministic byte costs, not
latency or game measurements. Actual planners may use shorter headers.

Existing caller/facade defaults remain v1. A v2 bridge requires reliable dispatch.
It never silently drops required metadata into a v1 request.

## Standalone integration points

Python: profiles.mcbews_v2.codec.encode/decode, transport.fragment,
downstream_payloads, send_downstream(existing_scheduler, ...), ReassemblyPool.
TypeScript: bridge/v2/codec and bridge/v2/transport export the matching codec,
fragment, sendUpstream and ReassemblyPool. Send loops await one scheduled frame at a
time, preserve ordering and stop on failure/cancellation; no unbounded task chain.
Create `V2Bridge` and pass it as `v2` to a reliable `McbeServerFacade`. Discovery
starts once per connection in the background; `await bridge.ready(state.id)` returns
the selected version, features and v2 endpoint. Use `bridge.request(state.id, ...)`
for negotiated calls, and register Python handlers on `bridge.registry`. The addon
router exposes `getV2Endpoint(epoch)` and its existing capability registry.
The endpoint rejects a new call while its method's ordering group is active, or when
business capacity is exhausted. This allows reverse calls to other groups without
waiting on a group already held by an ancestor. Notifications receive no error reply.
For Python v2 requests, `V2Bridge(timeout_seconds=...)` sets one deadline for scheduler
admission, fragment sending and the reply wait; discovery has a separate timeout.
Timeout, close and cancellation stop unsent fragments. A frame already handed to the
transport may still complete and cannot be retracted.

Handlers receive transport attribution separately from metadata. Metadata identity
is untrusted; callers cannot set reserved `sdk.*` keys and may supply a trace ID
through the dedicated argument. Responses retain metadata. Disconnect fails waiters
and releases the connection's fragments; idle partial messages expire by TTL.

## Discovery and fallback

The v2 manifest defines the `mcbews:negotiate_v2` control event. Python sends
`MCBEWS2H|attempt`; the addon replies with a fresh routing epoch and its features.
Python confirms that challenge, and the addon sends `ready` after admitting the peer.
The matching endpoint and features are installed before the next frame is read.
The manifest specifies the complete control frames and limits.

One discovery deadline covers sends and replies. No answer selects v1; an unsupported
version, busy admission or incomplete confirmation fails explicitly. Ordinary chat
and native commands remain independent. V1 fallback exposes only the manifest's known
methods; required metadata and features are never discarded. streams require negotiated `stream/1`.

Up to 32 peers and 32 provisional challenges share one world pool. Each peer has an
independent endpoint, fragments and pending requests; a new peer never replaces another.
The 60-second lease is refreshed every 20 seconds, and facade disconnect sends a bounded
best-effort close. Unreachable peers expire; stale frames and confirmations cannot
reactivate their epochs. Already started handlers may finish after closure, but their
unsent replies are suppressed. No request or world mutation is replayed automatically.

Carrier source type and routing tokens do not prove WebSocket identity or authorize a
caller. Hosts must authenticate and authorize independently. A lost close relies on
lease expiry; a command already handed to the transport cannot be retracted.

## Request streams

Python registers an async generator with `bridge.streams.register(name, handler)`.
The handler receives `(name, params, CapabilityContext)` and yields JSON items.
`await bridge.stream(connection_id, name, params)` or `await endpoint.stream(name, params)`
returns an async iterator. Use `async with stream:` around consumption when stopping
before completion, or call `await stream.aclose()`. Cancelling a pending iterator read
also cancels that stream. Existing atomic capability registrations stay independent.

The addon registers producers with `registerV2Stream(name, handler)` in the bridge router,
or `endpoint.registerStream(name, handler)`. Handlers return an `AsyncIterable`.
`await endpoint.stream(name, params, options)` returns an async iterator; early exit from
`for await` invokes `return()` and cancellation. The existing `V2RequestCancellation`
handle can cancel one stream. Registration removal affects future calls only.

| Kind | Required fields beyond v/kind | Direction |
| --- | --- | --- |
| stream_open | id, method, params | Caller to producer |
| stream_item | id, seq, item | Producer to caller |
| stream_end | id, seq | Producer to caller |
| stream_error | id, seq, error | Producer to caller |
| stream_cancel | id | Caller to producer |

All kinds allow optional meta and reject other fields. IDs belong to the caller;
responses echo that ID. Each logical item has a fresh sender-owned transport ID and
may use multiple R11 fragments. Item sequence numbers are separate from fragment
indexes: first item is seq=0, end/error carries the next expected seq. Total length is
unknown until termination. An empty stream ends at seq=0. A sequence mismatch fails
with INVALID_MESSAGE and cancels the producer; unknown or late messages allocate no
stream. Duplicate opens cannot restart a producer within the tombstone window.
If the tombstone table itself is full, new opens are rejected without storing another
ID; duplicate suppression then has the same capacity boundary as R11.

Normal completion preserves already buffered items until consumed. Failure, local
cancellation and overload discard buffered items and settle the consumer once. Producer
exceptions map to HANDLER_FAILED; inactivity maps to TIMEOUT. Cancellation stops future
items and closes the producer iterator; it never rolls back world changes. Python
producers must allow task cancellation. JavaScript producers should cooperate with
`context.cancellation`: an arbitrary unresolved Promise cannot be forcibly unwound.

Manifest stream maxima: 16 active streams per endpoint (both directions, including
completed streams with unread items); 32 buffered items / 131072 item JSON UTF-8 bytes
per consumer; 262144 buffered bytes per endpoint. A producer only sends one logical
item at a time. Opening, producing the next item, and sending each message each have a
30000 ms idle deadline; receiving items refreshes the consumer idle timer. This is not
a total stream lifetime limit. Duplicate-open tombstones last 30000 ms with at most
2048 active producers plus tombstones. Limits can only be lowered through Python
`V2Bridge(stream_limits=...)` or addon `V2EndpointOptions.streamLimits`.

Slow consumers fail explicitly with LIMIT_EXCEEDED and send cancellation. Fixed send
intervals and transport pacing do not provide receiver backpressure. Control sends are
bounded; when their capacity or the transport is exhausted, remote idle expiry still
bounds the lifetime. Incomplete transport messages retain the R11 TTL and quotas;
logical stream cancellation does not require decoding incomplete fragments. Disconnect
clears the connection pool. Old peers fail capability checks rather than buffering a
whole response and presenting it as a stream.

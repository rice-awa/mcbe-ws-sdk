# MCBEWS/2 contract (opt-in)

Installed profiles/mcbews_v2/manifest.json and vectors.json are authoritative.
A separate interaction protocol inspired by JSON-RPC, this is not JSON-RPC compatible.
MCBEWS/1 assets, identifiers, imports, constructors and default routing stay unchanged.
Facade routing and negotiation await R12/R13; continuous streams await R14.

## Logical messages

Strict JSON object fields follow the manifest: v is exactly MCBEWS/2; kind is request,
response or notification. Each kind lists required, optional and forbidden fields;
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
16 manifest tokens. stream/1 reserves future negotiation only; stream frames are not
accepted. Identity never authorizes a caller. Unknown reserved keys are rejected.
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

Existing caller/facade remains v1. Explicit standalone v2 modules support codec/carrier
building blocks on both ends. V2 to v1 is unsupported with no automatic metadata loss.
Routing, negotiation and streaming remain future tasks.

## Standalone integration points

Python: profiles.mcbews_v2.codec.encode/decode, transport.fragment,
downstream_payloads, send_downstream(existing_scheduler, ...), ReassemblyPool.
TypeScript: bridge/v2/codec and bridge/v2/transport export the matching codec,
fragment, sendUpstream and ReassemblyPool. Send loops await one scheduled frame at a
time, preserve ordering and stop on failure/cancellation; no unbounded task chain.
R12 must pass trusted attribution, share the pool, tick prune, cancel transfers and
close on disconnect; R13 must enforce fresh negotiated epochs and reject old frames.
The pool itself performs no negotiation, application dispatch or identity verification.

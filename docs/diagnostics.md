# Tracing and packet diagnostics

Diagnostics cover a connection from transport setup through protocol
negotiation and request processing to shutdown. The SDK's diagnostic channel
records only summaries by default, and you have to opt in to record full packets.

## Lifecycle stages

Every connection records at least:

| Stage | Recorded |
| --- | --- |
| `WS_TRANSPORT_CONNECTED` | Connection identifier and transport establishment |
| `NEGOTIATING` | Attempt, protocol version, feature set, timeout |
| `READY_V2` | Active epoch, negotiated features, session |
| `REJECTED` | Reason, close code and whether the transport was closed by us |
| `SESSION_INVALID` | Revocation or stale-session reason |
| `CLOSING` / `CLOSED` | Close code, bounded close reason, initiator, cleanup summary |

The reasons a negotiation can fail are a fixed set: `UNSUPPORTED_PROTOCOL`,
`NEGOTIATION_TIMEOUT`, `NEGOTIATION_CONFIRMATION_TIMEOUT`, `NEGOTIATION_BUSY`,
`INVALID_NEGOTIATION` and `SESSION_INVALID`. A rejected connection does not count
as a business disconnect, so it never produces a business `on_connected` /
`on_disconnected` pair.

When a connection is closed because negotiation was rejected, the close code is
`1008` and the close reason is at most 123 UTF-8 bytes. If you cannot observe who
initiated the close or why, record `unknown` instead of guessing.

For an EOF that carries no close code or reason, keep the transport error and
record the cause as unknown until you can trace it.

## Default summary vs raw payloads

By default the SDK's diagnostic channel records identifiers, stages, sizes and
frame classification. It leaves out business text, commands and packet bodies.
The example servers also log commands, capability parameters and results at INFO
level. Those are application logs: they are separate from the diagnostic channel
and are not covered by its redaction settings.

The examples' `--log-raw` option turns on complete outbound packet logs, so leave
it off in normal use. Before you handle private player data, review your
application logging as well.

```python
from mcbe_ws_sdk import DiagnosticSettings, McbeServer, trace_scope

async def inspect_record(record):
    pass  # Hand off to your collector; keep synchronous work brief.

server = McbeServer(
    diagnostics=DiagnosticSettings(
        full_packets=False,           # opt in explicitly when needed
        connection_ids=frozenset({"connection-uuid"}),
        secrets=("a-secret-value",),
        queue_maxsize=256,
    ),
    diagnostic_observers=(inspect_record,),
)

with trace_scope(trace_id="host-operation"):
    pass  # await state.command_client.run(...) or server.v2.request(...)
```

Creating the server does not change the global logging configuration. Call
`configure_logging()` yourself, or set up your host's own logging.

## Identifiers and stages

`current_trace()` returns immutable trace information: `connection_id`, `player`,
`request_id` and `trace_id`. Managed business tasks, event tasks, sender tasks and
`enqueue_response()` all inherit the context that was current when the work was
submitted. Writing directly to the public `response_queue` still works, but those
writes carry no context from the operation that produced them.

Every native command has its own `request_id`. A capability call uses a logical
request ID, which is a different identifier from the UUID of the command request
that carries it (`wire_request_id`). Both inherit the current trace. When a reply
arrives, the SDK first restores the pending request's original player and trace,
and then pairs the reply with its request. One trace can span many requests.
Unsolicited inbound frames get a new local trace.

| Stage | Meaning |
| --- | --- |
| `received` | A frame arrived, including malformed frames and external output |
| `processing_started/completed/rejected/failed` | Managed business processing started, finished, was rejected or failed |
| `request_registered` | The waiter for a native command or capability call was registered before sending |
| `enqueued` | The scheduler accepted the frame into its queue; this is not delivery |
| `prepared` | The transport is about to send the frame; this is not delivery |
| `transport_sent/transport_failed` | The transport call returned normally or raised an error |
| `remote_success/remote_failure` | The native `statusCode` or the remote protocol result |
| `remote_response` | A native reply without `statusCode`; no success is claimed |
| `request_failed/request_timeout` | A local failure, cancellation, remote error frame, disconnect or timeout |

A successful transport call does not mean the game executed the command or that
the addon's business logic succeeded. A native command can return a nonzero
`statusCode`, and a remote MCBEWS/2 error raises `RemoteCallError` with a `code`.
Diagnostics only classify these outcomes. They never change what a call returns
or raises. A reply may arrive before the transport call returns, and a
cancellation or timeout does not mean the remote side rolled anything back.

## Redaction and resource bounds

`full_packets=True` includes inbound packets at the `received` stage and outbound
packets at the `prepared` stage. `connection_ids` and `request_ids` are combined
with AND, so a record must satisfy both. A request filter can match either the
logical request ID or the wire request ID, and an empty filter lets every record
through.

`secret_keys` masks the values of matching JSON keys, ignoring case (by default
`password`, `token`, `secret` and `authorization`). `secrets` replaces literal
values inside strings, which also covers command or bridge JSON embedded in a
string. Redaction runs before logging and before every observer, and each
observer receives its own private copy.

When a full packet is too large, its body is omitted and the record carries
`packet_omitted="size_limit"`; `max_packet_bytes` defaults to 1 MiB. If parsing,
encoding or redaction fails unexpectedly, the whole record is dropped. Its
contents are not logged, and the SDK never falls back to the unredacted data.
Redaction handles at most 64 levels of nesting, counting the diagnostic record
itself.

`state.diagnostics` exposes `dropped_count`, `preparation_failures`,
`observer_failures` and `task_count`. Preparation failures also count toward
`dropped_count`. The queue holds at most `queue_maxsize` pending records plus one
record being processed, and when it is full a new record is discarded
immediately instead of waiting.

Exceptions from loggers and observers are isolated. A slow async observer only
delays later diagnostics; it does not block the reader, reply pairing or the
transport. Observers run in registration order on a single queue. On close, the
SDK discards pending records and cancels and awaits the worker. It starts no
background threads, so a synchronous CPU-heavy callback or a blocking log handler
still blocks the event loop and has to be moved out by the host. Observers must
also cooperate with asyncio cancellation.

The cache that correlates recent wire acknowledgements has a size limit. If an
entry is evicted, or a reply is late or duplicated, the diagnostic context for it
may be lost. The pending native and logical waiters stay authoritative, so no
request pairing depends on diagnostics being delivered.

## Echo classification

A `PlayerMessage` event is treated as external output based only on where Bedrock
says it came from: the sender is `External` or its localized equivalent, and
`type` does not explicitly mark it as player or chat. This is a noise label. It
is not authentication, and it does not prove that the message matches something
you sent. Player text that happens to be identical is still ordinary input.

`commandResponse` and `error` frames always go through request pairing first, so
they can never be swallowed as echoes. Raw full diagnostics keep the
`external_echo` records. The examples use the same `is_external_echo()` helper
before replying to external output.

`RAW_INBOUND` and `RAW_OUTBOUND` keep their original arguments, ordering and
unredacted data, and `RAW_OUTBOUND` is still the historical pre-send event. If
you want redacted, size-limited observation with explicit transport results, use
the separate diagnostic channel.

## Logging failures

A failure to build or deliver a diagnostic record must never stop request
processing. Dropped records and preparation failures are counted and can be read
from `state.diagnostics`. Protocol and transport errors stay visible, because
diagnostics do not replace error handling.

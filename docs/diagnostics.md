# Tracing and packet diagnostics

Diagnostics answer four questions: did the transport connect, did admission
succeed or fail, what did the business path do, and why did the connection
close. Default logs are summaries; full payloads are opt-in.

## Lifecycle stages

Every connection records at least:

| Stage | Recorded |
| --- | --- |
| `WS_TRANSPORT_CONNECTED` | Connection identifier and transport establishment |
| `NEGOTIATING` | Attempt, protocol version, feature set, timeout |
| `READY_V2` | Admitted epoch, negotiated features, session |
| `REJECTED` | Reason, close code and whether the transport was closed by us |
| `SESSION_INVALID` | Revocation or stale-session reason |
| `CLOSING` / `CLOSED` | Close code, bounded close reason, initiator, cleanup summary |

Stable admission reasons are `UNSUPPORTED_PROTOCOL`, `NEGOTIATION_TIMEOUT`,
`NEGOTIATION_CONFIRMATION_TIMEOUT`, `NEGOTIATION_BUSY`,
`INVALID_NEGOTIATION` and `SESSION_INVALID`. A rejected connection is not a
business disconnect and does not produce a business `on_connected` /
`on_disconnected` pair.

Close code is `1008` for admission rejection. The close reason is at most 123
UTF-8 bytes. If the close initiator or reason is not observable, record
`unknown` rather than guessing.

A zero-byte EOF is still an unconfirmed condition. Do not classify it as normal
and do not suppress all `websockets` errors to hide it. Keep the evidence and
turn it into a specific classification only when the source is known.

## Default summary vs raw payloads

Default diagnostics record identifiers, stages, sizes and frame
classification. Business text, commands and packet bodies are omitted. The
bundled example servers accept an explicit `--log-raw` flag to include full
payloads; without it, raw payloads stay disabled. `--log-raw` is opt-in for a
reason: payloads may contain player text and application secrets.

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

Construction does not change global logging configuration. Call
`configure_logging()` or configure your host's logging explicitly.

## Identifiers and stages

`current_trace()` returns immutable `connection_id`, `player`, `request_id` and
`trace_id` metadata. Managed business tasks, event tasks, sender tasks and
`enqueue_response()` carry the submission context. Direct writes to the public
`response_queue` keep working but carry no originating operation context.

A native command has its own `request_id`. A capability call has a logical
request ID distinct from the surrounding command-request UUID
(`wire_request_id`). Both inherit the current trace; replies restore the pending
request's original player and trace before pairing. One trace can span many
requests. Unsolicited inbound frames receive a new local trace.

| Stage | Meaning |
| --- | --- |
| `received` | A frame arrived, including malformed or external-output frames |
| `processing_started/completed/rejected/failed` | Managed business processing |
| `request_registered` | A native or capability waiter exists before sending |
| `enqueued` | Scheduler admission; not delivery |
| `prepared` | Transport is about to send; not delivery |
| `transport_sent/transport_failed` | The transport call returned or raised |
| `remote_success/remote_failure` | Native `statusCode` or remote protocol result |
| `remote_response` | Native reply without `statusCode`; no success claim |
| `request_failed/request_timeout` | Local failure, cancellation, remote error frame, disconnect or timeout |

Transport success does not mean game execution or addon business success.
Native commands can return a nonzero `statusCode`; remote MCBEWS/2 errors raise
`RemoteCallError` with a `code`. Diagnostics classify these results without
changing the call contract. A reply may arrive before the transport call
returns. Cancellation and timeout do not imply the remote side rolled back.

## Redaction and resource bounds

`full_packets=True` includes inbound `received` and outbound `prepared` packets.
`connection_ids` and `request_ids` are ANDed; either a logical or wire request
ID can match. Empty filters allow every record.

`secret_keys` masks JSON dictionary values case-insensitively (`password`,
`token`, `secret`, `authorization` by default). `secrets` replaces literal
values inside strings, including embedded command or bridge JSON. Redaction runs
before logging and every observer; each observer receives a private copy.

Oversized full packets omit the body and record `packet_omitted="size_limit"`;
`max_packet_bytes` defaults to 1 MiB. Unexpected parsing, encoding or redaction
failures drop the whole record without logging its contents or falling back to
unredacted data. Redaction limits nesting to 64 levels, including the
diagnostic record itself.

`state.diagnostics` exposes `dropped_count`, `preparation_failures`,
`observer_failures` and `task_count`. Preparation failures count toward
`dropped_count`. The queue holds at most `queue_maxsize` pending records plus
one active callback; a full queue discards observations immediately.

Logger and observer exceptions are isolated. A slow async observer delays later
diagnostics but does not block the reader, reply pairing or transport. Observers
run in registration order on one lane. Close discards pending records and
cancels/joins the worker. There are no background threads; synchronous
CPU-heavy callbacks or blocking log handlers still block the event loop and must
be moved out. Observers must cooperate with asyncio cancellation.

Recent wire acknowledgements use a bounded correlation cache; eviction or a
late/duplicate reply may lose diagnostic context. Pending native and logical
waiters remain authoritative. No request pairing depends on diagnostics being
delivered.

## Echo classification

External-output `PlayerMessage` events are classified by Bedrock provenance:
sender `External` or its localized equivalent, unless `type` explicitly
identifies player/chat. This is a noise label, not authentication and not proof
of an outbound match. Equal player text remains ordinary input.

`commandResponse` and `error` frames always take their request-pairing path
first and cannot be swallowed as echoes. Raw full diagnostics retain
`external_echo` records. Examples use the same `is_external_echo()` helper
before replying to external output.

`RAW_INBOUND` and `RAW_OUTBOUND` keep their original arguments, ordering and
unredacted data; `RAW_OUTBOUND` is still the historical pre-send event. Use the
separate diagnostic lane for bounded, redacted observation with explicit
transport results.

## Logging failures

A failure to build or deliver a diagnostic record must not stop request
processing. Dropped records and preparation failures are counted and can be
exported through `state.diagnostics`. Protocol or transport errors remain
visible; diagnostics do not replace error handling.

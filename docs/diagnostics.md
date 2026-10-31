# Tracing and packet diagnostics

The facade creates one bounded diagnostic lane per connection in both dispatch
modes. Defaults record metadata at DEBUG: identifiers, stages, sizes and frame
classification. Business text, commands and packet bodies are omitted.
No logging configuration changes occur on construction; hosts choose their own
configuration or explicitly call configure_logging().

~~~python
from mcbe_ws_sdk import DiagnosticSettings, McbeServerFacade, trace_scope

async def inspect_record(record):
    pass  # Forward to your diagnostic collector; keep synchronous work brief.

facade = McbeServerFacade(
    diagnostics=DiagnosticSettings(
        full_packets=True,
        connection_ids=frozenset({"connection-uuid"}),
        secrets=("a-secret-value",),
        queue_maxsize=256,
    ),
    diagnostic_observers=(inspect_record,),
)

# In a host callback, multiple calls can share an explicit operation trace.
with trace_scope(trace_id="host-operation"):
    pass  # await state.command_client.run(...), await client.request(...)
~~~

## Identifiers and stages

current_trace() returns immutable connection_id, player, request_id and trace_id
metadata. Managed business tasks, event tasks, sender tasks and enqueue_response()
carry the submission context. Direct writes to the public response_queue retain
legacy behavior but have no originating operation context.

A native command has its own request_id. An addon capability has a logical
request_id distinct from the surrounding commandRequest UUID (wire_request_id).
Both inherit the current trace; replies restore the pending request's original
player and trace before pairing. traceId is represented by trace_id in Python and
logs; it is never added to MCBEWS/1 wire data. One trace can span many requests.
Unsolicited inbound frames receive a new local trace.

- received: a frame arrived, including malformed or external-output frames.
- processing_started/completed/rejected/failed: managed business processing.
- request_registered: a native or capability waiter exists before sending.
- enqueued: reliable scheduler admission; this is not delivery.
- prepared: transport is about to send; this is not delivery either.
- transport_sent/transport_failed: the transport call returned or raised.
- remote_success/remote_failure: the native statusCode or capability ok result.
- request_failed/request_timeout: local failure, cancellation, remote error frame,
  disconnect or timeout. These do not imply remote execution was rolled back.

Transport success does not mean game execution or addon business success.
Legacy native commands still return nonzero statusCode replies; capability calls
still return their original dictionaries, including ok=False. Diagnostics classify
these results without changing the return contract. Missing native statusCode is
reported as remote_response, with no success claim. A request may receive its reply
before its transport call returns.

## Privacy and resource bounds

full_packets=True includes inbound received and outbound prepared packets. Filters
connection_ids and request_ids are ANDed; either a logical or wire request ID can
match. Empty filters allow every record. secret_keys masks JSON dictionary values
case-insensitively (password, token, secret, authorization by default). secrets
replaces literal values in strings, including embedded command/bridge JSON;
configure these for application secrets carried inside string fields. Redaction
runs before logging and every observer. Each observer receives a private copy.
Oversized full packets omit the body and record packet_omitted="size_limit";
max_packet_bytes defaults to 1 MiB. Full mode requires deliberate host opt-in.

Packet preparation is best-effort: unexpected parsing, encoding or redaction failures drop
the record without logging its contents or using an unredacted fallback. Redaction
limits nesting to 64 levels, including the diagnostic record itself.

state.diagnostics exposes dropped_count, preparation_failures, observer_failures
and task_count. Preparation failures count toward dropped_count.
The queue holds at most queue_maxsize pending records plus one active callback;
full queues discard observations immediately. Logger/observer exceptions are
isolated. A slow async observer cannot block the reader, request pairing or
transport. Observers run in registration order on one lane; one slow observer
can delay later diagnostics. Close discards pending records and cancels/joins the
worker. There are no background threads. Synchronous CPU-heavy callbacks or
blocking logging handlers still block the event loop; hosts must move such work
outside it. Observers must cooperate with asyncio cancellation.

Recent wire acknowledgements use a queue_maxsize-bounded correlation cache;
eviction or repeated/late replies may lose that diagnostic context. Pending
native and logical capability waiters remain authoritative and independent of
this cache. No request pairing depends on diagnostics being delivered.

## Echo classification and compatibility

External-output PlayerMessage events are classified using Bedrock provenance
(sender External or its localized equivalent, unless type explicitly identifies
player/chat). This is a noise label, not authentication or proof of an outbound
match. Equal player text remains ordinary input. commandResponse and error
frames always take their request-pairing path first and cannot be swallowed as
echoes. Raw full diagnostics retain external_echo records. Examples use the same
is_external_echo() helper before replying to external output.

Existing RAW_INBOUND and RAW_OUTBOUND callbacks retain their original arguments,
ordering, and unredacted data; RAW_OUTBOUND means the historical pre-send event.
Their legacy inline behavior is unchanged. Use the separate diagnostic lane for
bounded, redacted observation with explicit transport results.

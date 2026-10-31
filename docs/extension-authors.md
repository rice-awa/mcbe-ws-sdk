# Extension authors

These APIs belong to the **unreleased checkout**, not PyPI `0.2.1`.
Start with [getting started](getting-started.md); use the [migration guide](migration.md)
when retaining an existing host. Extensions register handlers; they do not own
request wait tables, wire IDs or fragment state.

## Register a Python capability

Register on the `V2Bridge.registry` used by the server before accepting calls:

```python
from mcbe_ws_sdk import CapabilityContext, McbeServer, V2Bridge

bridge = V2Bridge()
server = McbeServer(v2=bridge)

async def echo(name, payload, context: CapabilityContext):
    return {"ok": True, "payload": dict(payload), "metadata": dict(context.metadata)}

def validate(payload, context):
    return None if "text" in payload else "text is required"

registration = bridge.registry.register("demo.echo", echo, validate=validate)
# bridge.registry.unregister(registration)
```

Handlers receive `(name, payload, CapabilityContext)` and return a mapping, or
an awaitable resolving to one. A validator returns `None` or a rejection message.
Duplicate names require explicit `replace=True`. Removal affects future calls;
a stale registration cannot remove its replacement. In-flight calls keep their
selected handler. Unknown methods, invalid input and handler exceptions map to
`UNSUPPORTED_METHOD`, `INVALID_PARAMS` and `HANDLER_FAILED` on v2.

`context.client` is the connection's v2 endpoint for reverse requests and
notifications. Calls into an already active method group are rejected to avoid
recursive queue waits. Metadata identity is untrusted; transport attribution
and application authorization remain separate.

## Register an addon capability

In your addon composition, register through its exported router API:

```typescript
import { registerCapability } from "./bridge/router";

registerCapability("demo.echo", (_name, payload, context) => ({
  ok: true,
  payload,
  metadata: context.metadata,
}));
```

Custom handlers coexist with built-ins and need no SDK changes. Build the addon
and import the generated pack; in the target world enable **Experiments → Beta
APIs**, then activate the behavior pack. Without Beta APIs scripts do not load.
See the [addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md).
This is not a claim of new platform or game-version support.

## Metadata and notifications

After `selection = await bridge.ready(state.id)`, use negotiated calls:

```python
response = await bridge.request(
    state.id, "demo.echo", {"text": "hello"},
    metadata={"demo.locale": "en", "demo.options": {"compact": True}},
    trace_id="demo-trace", require={"metadata/1"},
)
print(response["result"], response.get("meta"))
```

Remote v2 errors raise `RemoteCallError`; transport/timeout/cancellation failures
also end the call. A v1 fallback result remains inside `response["result"]`;
check its capability-level `ok` field. Any supplied metadata requires
`metadata/1`, so it cannot silently fall back to v1. Unknown optional keys and
nested values survive the round trip. Metadata is limited to 4096 compact JSON
UTF-8 bytes and the protocol depth/number rules. Caller-supplied `sdk.*` keys
are reserved; use `trace_id` for tracing. Identity fields never grant permission.

For v2-only notifications use `selection.endpoint.notify(...)` after checking
that an endpoint was negotiated. Notifications create no reply waiter, so remote
handler failure cannot be reported as a response. See [the protocol contract](protocol-v2.md).

## Native event subscriptions

Inside a managed hook:

```python
token = await state.native_events.subscribe("PlayerMessage", observe)
# Later: await state.native_events.unsubscribe(token)
```

`observe(event)` receives a `NativeEvent`. Observers cannot consume business
messages; their return values are ignored. Interceptors explicitly allow,
reject or transform business delivery. Registrations and observation queues are
bounded and cleaned up on disconnect. Other event names are opaque passthrough,
not proof that a game version supports them. See [native events](native-events.md).

## Continuous streams

Register a producer on the server's bridge:

```python
async def count(name, params, context):
    for value in range(params["count"]):
        yield {"value": value}

bridge.streams.register("demo.count", count)
```

A peer with the same producer registration can be consumed with:

```python
stream = await bridge.stream(state.id, "demo.count", {"count": 3})
async with stream:
    async for item in stream:
        print(item)
```

The addon counterpart is `registerV2Stream(name, handler)`; producers return an
`AsyncIterable`. Consumption starts before the final length is known. Large
items are fragmented by the SDK and delivered once as logical items.
Use the context manager or `await stream.aclose()` for early exit. Cancelling a
pending read cancels that stream, not other requests. Producers must cooperate
with cancellation; JavaScript must observe `context.cancellation`.

Streams require negotiated `stream/1`. Default limits include 16 active streams,
32 buffered items and 131072 buffered item bytes per consumer, with 262144 bytes
per endpoint. Slow consumers fail with `LIMIT_EXCEEDED`; there is no receiver
credit mechanism. Fixed delays do not provide receiver backpressure. Failure or
cancellation discards queued items and cannot undo world mutations. See the
[stream contract](protocol-v2.md#request-streams) for deadlines and duplicate
suppression limits.

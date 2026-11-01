# Extension authors

Extensions register handlers; they do not own request wait tables, wire IDs or
fragment state. Start with [getting started](getting-started.md); use
[migration](migration.md) only when updating an existing host.

## Register a Python capability

Register on the `V2Bridge` used by the server:

```python
from mcbe_ws_sdk import CapabilityContext, McbeServer, V2Bridge

bridge = V2Bridge()
server = McbeServer(v2=bridge)


async def echo(name: str, payload: dict, context: CapabilityContext) -> dict:
    return {"ok": True, "payload": dict(payload), "metadata": dict(context.metadata or {})}


def validate(payload: dict, context: CapabilityContext) -> str | None:
    return None if "text" in payload else "text is required"


registration = bridge.registry.register("demo.echo", echo, validate=validate)
# bridge.registry.unregister(registration)
```

Handlers receive `(name, payload, CapabilityContext)` and return a mapping, or
an awaitable resolving to one. A validator returns `None` to accept or a message
to reject the payload.

Duplicate names require explicit `replace=True`. Removal affects future calls;
a stale registration cannot remove its replacement. In-flight calls keep the
handler selected when they started. Unknown methods, invalid input and handler
exceptions map to `UNSUPPORTED_METHOD`, `INVALID_PARAMS` and `HANDLER_FAILED`
on the wire.

`context.client` is the connection's V2 endpoint for reverse requests,
notifications and streams. Calls into an already active method group are
rejected to avoid recursive queue waits. Metadata identity is untrusted;
transport attribution and host authorization remain separate.

## Register an addon capability

In your addon composition, register through the router API:

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("demo.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
// unregisterCapability(registration)
```

Handlers receive `(method, payload, context)` and return an object or a Promise.
Context includes `caller`, `requestVersion`, `metadata`, `client` and
`cancellation`. An optional validator returns a message to reject the payload.

Custom handlers coexist with the built-ins and need no SDK changes. Duplicate
names throw unless `replace: true` is passed. Unregistering during an in-flight
call does not change that call.

There is no aggregate `setCapabilityHandler` entry in this version. Register one
handler per name.

## Register a stream producer

Python:

```python
async def ticks(name: str, params: dict, context: CapabilityContext):
    for index in range(params.get("count", 5)):
        yield {"index": index}

bridge.streams.register("demo.ticks", ticks)
```

Addon:

```typescript
import { registerV2Stream } from "./bridge/router";

const unregister = registerV2Stream("demo.ticks", async function* (_method, params, context) {
  for (let index = 0; index < (params.count ?? 5); index += 1) {
    if (context.cancellation?.cancelled) return;
    yield { index };
  }
});
```

A producer yields JSON items and does not need to know the total length.
Large items are fragmented by the SDK and delivered once as logical items.
Registering a duplicate stream name throws.

## Call the addon

After admission, a managed hook can use the bridge:

```python
selection = await server.v2.ready(state.id)     # explicit admission wait
response = await server.v2.request(
    state.id,
    "demo.echo",
    {"text": "hello"},
    metadata={"demo.locale": "en", "demo.options": {"compact": True}},
    trace_id="demo-trace",
    require={"metadata/1"},
)
print(response["result"], response.get("meta"))

await server.v2.notify(state.id, "demo.event", {"value": 1})

stream = await server.v2.stream(state.id, "demo.ticks", {"count": 3})
async with stream:
    async for item in stream:
        print(item)
```

`ready()` is only needed when work starts outside a hook. Hooks already run
after `READY_V2`.

A remote protocol error raises `RemoteCallError` with `code` and a bounded
message. Transport failures, timeouts and cancellation also end the call.
Notifications create no reply waiter, so a remote handler failure cannot be
returned as a response.

Metadata is limited to 4096 compact JSON UTF-8 bytes and follows the protocol
depth and number rules. Optional unknown keys and nested values survive the
round trip. Caller-supplied `sdk.*` keys are reserved; use `trace_id` for
tracing. Identity fields never grant permission.

## Cancellation and errors

- Cancelling a pending Python request ends its wait. A frame already handed to
  the transport may still deliver, and an executed world change is not undone.
- Addon callers use a `V2RequestCancellation` handle per request; call
  `cancel()` and check `cancelled` before sending more work.
- Stream consumers exit with `async with` or `await stream.aclose()`; cancelling
  a pending read cancels that stream, not other requests.
- Producers must cooperate with cancellation. JavaScript producers should check
  `context.cancellation`; an arbitrary unresolved Promise cannot be forcibly
  unwound.
- Player-facing failures show the method or capability, the error code, a
  readable message of at most 256 UTF-8 bytes, and an action hint. Internal
  request IDs and stack traces stay in logs.

## Limits

| Resource | Default |
| --- | --- |
| Message size | 65536 UTF-8 bytes |
| Metadata | 4096 compact JSON UTF-8 bytes |
| Downlink command line | 461 UTF-8 bytes, lowerable only |
| Uplink carrier frame | 256 Unicode code points and 1024 UTF-8 bytes |
| Active streams per endpoint | 16 both directions |
| Buffered items per consumer | 32 items / 131072 item bytes |
| Buffered bytes per endpoint | 262144 |
| Stream idle deadline | 30000 ms per open/item/message |

A slow consumer fails with `LIMIT_EXCEEDED` and sends a cancellation. Fixed
send intervals do not provide receiver backpressure; there is no receiver-credit
mechanism. See [the protocol contract](protocol-v2.md#request-streams).

## Build variants and demo registration

The default addon build registers exactly `get_player_snapshot` and
`get_inventory_snapshot`. The complete demo build registers `demo.echo`, the
`demo.ticks` stream and the restricted `run_world_command` handler:

```bash
cd addon
npm install
npm run build:demo
npm run mcaddon:demo
```

`--demo` or `MCBE_DEMO=1` selects `main.demo.ts`; the output path is still
`dist/scripts/main.js`. To add your own handlers, import them in `main.ts` (or
your own entry) and register once during `initializeAfterWorldLoad`.

`run_world_command` is never part of the basic package. When registered, its
default allowlist is `say` and the dangerous-command denylist still applies.
Do not widen the policy without host-side authorization.

The addon must be imported into the target world with **Experiments → Beta
APIs** enabled. Automated examples do not establish live-game support; verify
your game version and platform separately.

## Trust boundary

The bridge is not a security boundary. Caller attribution, the simulated
`MCBEWS_BRIDGE` player, metadata identity and addon-side command filtering do
not replace host authentication and authorization. Enforce caller permissions in
your Python capability handlers.

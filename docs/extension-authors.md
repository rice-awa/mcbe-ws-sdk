# Extension authors

An extension only registers handlers. It does not keep a table of pending
requests, assign wire IDs or hold fragment state, because the SDK does all of
that. If you are new here, start with [getting started](getting-started.md), and
turn to [migration](migration.md) only when you are updating an existing host.

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

A handler receives `(name, payload, CapabilityContext)` and returns a mapping, or
an awaitable that resolves to one. A validator returns `None` to accept the
payload, or a message to reject it.

Registering a name that already exists requires an explicit `replace=True`.
Unregistering affects only future calls, and an outdated registration handle
cannot remove the registration that replaced it. A call already in progress
keeps the handler that was chosen when it started. On the wire, an unknown
method, invalid input and a handler exception map to `UNSUPPORTED_METHOD`,
`INVALID_PARAMS` and `HANDLER_FAILED`.

`context.client` is the V2 endpoint of the current connection, which you can use
to send requests, notifications and streams back to the caller. A call into a
method group that is already running is rejected, so a handler cannot end up
waiting on its own queued work. The identity in metadata is untrusted; see
[Trust boundary](#trust-boundary).

## Register an addon capability

In your addon composition, register through the router API:

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("custom.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
// unregisterCapability(registration)
```

A handler receives `(method, payload, context)` and returns an object or a
Promise. The context includes `caller`, `requestVersion`, `metadata`, `client`
and `cancellation`. An optional validator returns a message to reject the
payload.

Your handlers sit alongside the built-in ones, and you do not need to change the
SDK. Registering a duplicate name throws unless you pass `replace: true`.
Unregistering a capability while a call is running does not affect that call.

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

const unregister = registerV2Stream("custom.ticks", async function* (_method, params, context) {
  for (let index = 0; index < (params.count ?? 5); index += 1) {
    if (context.cancellation?.cancelled) return;
    yield { index };
  }
});
```

A producer yields JSON items one at a time and does not need to know the total
length in advance. The SDK fragments large items and delivers each one once, as
a single logical item. Registering a stream name that already exists throws.

## Call the addon

After negotiation, a managed hook can use the bridge:

```python
selection = await server.v2.ready(state.id)     # explicit negotiation wait
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

You only need `ready()` when the work starts outside a hook, because hooks
already run after `READY_V2`.

When the remote side returns a protocol error, the call raises `RemoteCallError`
with the `code` and a short message. A transport failure, a timeout or a
cancellation also ends the call. A notification has no reply to wait for, so if
the remote handler fails, that failure cannot come back as a response.

Metadata is limited to 4096 UTF-8 bytes of compact JSON and follows the protocol
rules for depth and numbers. Unknown optional keys and nested values come back
unchanged. Keys matching `sdk.*` are reserved and cannot be set by the caller;
pass `trace_id` to trace a request. An identity field never grants
permission.

## Cancellation and errors

- Cancelling a pending Python request ends the wait. A frame that was already
  handed to the transport may still be delivered, and a world change that has
  already run is not undone.
- Addon callers use one `V2RequestCancellation` handle per request. Call
  `cancel()`, and check `cancelled` before sending more work.
- A stream consumer that may stop early should use `async with` or
  `await stream.aclose()`. Cancelling a pending read cancels that stream only,
  not other requests.
- Producers have to cooperate with cancellation. JavaScript producers should
  check `context.cancellation`, because a Promise that never resolves cannot be
  forced to stop.
- When a call fails in front of a player, show the method or capability, the
  error code, a readable message of at most 256 UTF-8 bytes and an action hint.
  Keep internal request IDs and stack traces in the logs.

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

A slow consumer fails with `LIMIT_EXCEEDED`, and the SDK sends a cancellation.
Sending at a fixed interval does not give the receiver backpressure, and there
is no receiver-credit mechanism. See
[the MCBEWS/2 protocol](protocol-v2.md#request-streams).

## Build variants and demo registration

The default addon build registers every bundled capability: `get_player_snapshot`,
`get_inventory_snapshot`, `demo.echo`, the `demo.ticks` stream and
`run_world_command`. The lean `--base` build registers only
`get_player_snapshot` and `get_inventory_snapshot`. The build commands for both
are in the [addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md#build).

`--base` or `MCBE_BASE=1` selects `main.ts`, while the default entry is
`main.demo.ts`. Both write to `dist/scripts/main.js`. To add your own handlers,
import them in your entry and register them once during
`initializeAfterWorldLoad`.

`run_world_command` applies the command denylist by default. To narrow it
further, pass an explicit allowlist to `handleRunWorldCommand`. The addon must
be imported into a world with **Experiments → Beta APIs** enabled, as described
in the [addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md#enable-in-a-world). Automated examples
do not establish live-game support, so verify your game version and platform
separately.

## Trust boundary

The bridge is not a security boundary. None of the following replaces
authentication and authorization on your host: where a message was delivered
from, the simulated `MCBEWS_BRIDGE` player, the identity in metadata, and the
command filtering in the addon. Check each caller's permissions in your Python
capability handlers, whatever policy the addon applies.

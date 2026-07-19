# Capability Greeting Example

A minimal, zero-dependency smoke-test that demonstrates registering a
`CapabilityHandler` and running a `McbeServerFacade` lifecycle — no real
Minecraft server or LLM worker is required.

## What's This

This example shows how to register a custom capability handler (here `"greet"`,
which returns a friendly greeting dict) and wire it into a `McbeServerFacade`.
The facade starts a WebSocket listener, idles for 3 seconds, then shuts down
cleanly — exercising the startup and shutdown paths without a connecting
client.

## Prerequisites

- Python 3.11+
- SDK installed from the repo root:

  ```bash
  pip install -e ./mcbe-ws-sdk
  ```

## Run

```bash
cd examples/capability-greeting
python greeting.py
```

Expected output (representative log lines):

```
[greeting] INFO Starting capability-greeting facade...
[... facade_listening host=0.0.0.0 port=8080 ...]
[greeting] INFO Facade stopped.
```

The facade listens on `0.0.0.0:8080` by default (configurable via
`GatewaySettings` in the constructor).

## How It Works

1. **Facade assembly**: `McbeServerFacade` is built with a hook, a capability
   registry, and defaults for all remaining collaborators (transport, sink,
   protocol handler, addon service).

2. **Capability registration**: The `CapabilityRegistry` maps the string
   `"greet"` to a `GreetingHandler` instance. Any unregistered capability
   name is handled by the built-in `LoggingStubHandler`, which returns a safe
   error payload instead of raising `KeyError`.

3. **Lifetime**: `run_lifetime()` starts the WebSocket listener on the
   default host/port. The script sleeps for 3 seconds, then calls `stop()`
   to signal a graceful shutdown. `shutdown_all()` tears down any active
   connections and the hook's `on_disconnected` fires for each one.

4. **No client needed**: Without a real MCBE client connecting, the hook's
   `on_connected` won't fire — this is a startup / shutdown lifecycle smoke
   test. To drive the full path (including `on_bridge_message` where an
   inbound `scriptevent mcbeai:bridge_request` is routed to the registry),
   you can feed a fake WebSocket through `facade._on_connection(...)`.

## Next Steps

- See `ConnectionHook.on_bridge_message` — that hook receives inbound
  `scriptevent mcbeai:bridge_request` frames; wire
  `registry.handle(ctx)` there to dispatch capability calls.
- Review `CapabilityContext` for the fields available to every handler
  (connection id, player name, capability name, payload, request id, and the
  transport `send` back-reference).
- Check the `McbeServerFacade` constructor signature (`settings=`, `hook=`,
  `sink=`, `addon=`, `registry=`, `capabilities=`) for production wiring.

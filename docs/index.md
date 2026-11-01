# Connect Minecraft to Python

**mcbe-ws-sdk** is a WebSocket gateway for Minecraft Bedrock Edition. Handle
player messages in Python, execute game commands, call addon capabilities and
receive streams without managing packet fragments or request wait tables.

**The current protocol is V2 (MCBEWS/2).** Connections complete protocol
negotiation before processing business messages. The SDK package version and
protocol version are independent.

## Try it

From the repository root:

```bash
pip install -e ".[dev,docs]"
npm ci --prefix addon
npm --prefix addon run mcaddon:production
```

Import `addon/dist/packages/mc-ws-sdk.mcaddon`, enable **Experiments → Beta APIs**
in the target world and activate the behavior pack. The addon is required for
connection setup in both examples; without Beta APIs, scripts do not load.

```bash
python examples/addon-server/server.py
```

In Minecraft, run `/wsserver <python-host-ip>:8080`, then send `!inv` to query
your inventory. Try `!echo hello` or `!stream 5` next. Only messages starting
with ASCII `!` are handled; ordinary chat stays silent.

See [getting started](getting-started.md) for prerequisites, configuration and
troubleshooting.

## Build your host

- [Architecture](architecture.md): connection setup, queues and lifecycle.
- [Extension authors](extension-authors.md): capabilities, notifications, metadata and streams.
- [Native events](native-events.md): subscriptions, observers and interceptors.
- [Diagnostics](diagnostics.md): trace a request and interpret failures.
- [V2 protocol](protocol-v2.md): messages, negotiation, fragmentation and limits.
- [API reference](reference.md): classes, methods and parameters.
- [Migration](migration.md): upgrade from 0.2.x and roll back both deployment components.

The SDK owns transport and protocol handling. Your host owns authentication,
authorization and application state; `ConnectionHook` handles input and
`ResponseSink` renders replies.

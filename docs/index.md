# Connect Minecraft to Python

**mcbe-ws-sdk** is a WebSocket gateway for Minecraft Bedrock Edition. In Python
you can handle player messages, run game commands, call addon capabilities and
receive streams, without tracking packet fragments or keeping your own table of
pending requests.

The SDK and the addon talk over **V2 (MCBEWS/2)**. Every connection negotiates
the protocol first, and your code only sees business messages after that
succeeds. The protocol version and the SDK package version are numbered
independently.

## Try it

From the repository root:

```bash
pip install -e ".[dev,docs]"
npm ci --prefix addon
npm --prefix addon run mcaddon:production
```

Import `addon/dist/packages/mc-ws-sdk.mcaddon`, enable **Experiments → Beta APIs**
in the target world and activate the behavior pack. Both examples need the addon
to set up the connection, and without Beta APIs its scripts never load.

```bash
python examples/addon-server/server.py
```

In Minecraft, run `/wsserver <python-host-ip>:8080`, then send `!inv` to query
your inventory. Next, try `!echo hello` or `!stream 5`. The example answers only
messages that start with ASCII `!`, so ordinary chat gets no reply.

[Getting started](getting-started.md) covers the prerequisites, configuration
and troubleshooting in more detail.

## Build your host

- [Architecture](architecture.md): how a connection is set up, how work is queued and how it ends.
- [Extension authors](extension-authors.md): add capabilities, notifications, metadata and streams.
- [Native events](native-events.md): subscribe to game events, observe them or intercept them.
- [Diagnostics](diagnostics.md): trace a request and understand a failure.
- [V2 protocol](protocol-v2.md): message format, negotiation, fragmentation and limits.
- [API reference](reference.md): classes, methods and parameters.
- [Migration](migration.md): upgrade from 0.2.x, or roll back both the Python package and the addon.

The SDK handles transport and the protocol. Your host handles authentication,
authorization and application state: a `ConnectionHook` receives input and a
`ResponseSink` renders replies.

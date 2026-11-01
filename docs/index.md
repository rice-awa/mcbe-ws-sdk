# mcbe-ws-sdk

A generic WebSocket gateway SDK for Minecraft Bedrock Edition. `McbeServer` owns
the transport, mandatory MCBEWS/2 admission, request correlation, byte-safe
command chunking, capabilities and request streams. Model services, message
brokers, authentication and business history stay in your host.

```text
Minecraft client  ←── /wsserver IP:port ──→  Python host
                                              ├── ConnectionHook / ResponseSink
                                              └── V2Bridge ←→ companion addon
```

## Quickstart

```bash
pip install -e ".[dev,docs]"                 # from this checkout
python examples/basic-server/server.py       # command demo, tellraw replies
```

In Minecraft: `/wsserver <host-ip>:8080`, then send `!help` or
`!wscmd time query daytime`.

The example handles only trimmed messages that start with ASCII `!`. Ordinary
chat, bare `help` / `帮助`, mid-sentence `!` and full-width `！` are silent with
no reply and no capability call. A bare `!` returns one usage hint; an unknown
`!command` returns one hint.

Addon capabilities need the companion pack and **Experiments → Beta APIs** in
the target world. Without Beta APIs the scripts do not load and admission times
out.

## Where to go next

- [Getting started](getting-started.md): install, addon, first server, commands
  and troubleshooting.
- [Architecture](architecture.md): admission state machine, runtime and bridge
  layers.
- [Extension authors](extension-authors.md): capabilities, notifications,
  metadata, events and streams.
- [Protocol (MCBEWS/2)](protocol-v2.md): required admission, carrier, limits and
  streams.
- [Diagnostics](diagnostics.md): lifecycle logs, `--log-raw`, redaction.
- [Native events](native-events.md): observers, interceptors and subscriptions.
- [Migration](migration.md): breaking changes from the dual-protocol release.
- [API reference](reference.md): generated from the public source.

Automated protocol coverage does not by itself prove live-game support. Verify
your game version, platform and addon loading before relying on a deployment.

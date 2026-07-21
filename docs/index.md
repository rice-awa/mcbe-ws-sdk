# mcbe-ws-sdk

Generic **WebSocket gateway SDK** for Minecraft Bedrock Edition.

It owns the **WS transport**, **packet protocol**, and **byte-safe command
chunking** (461-byte hard limit). Your host injects behaviour through two
protocols — `ConnectionHook` and `ResponseSink` — and drives the stack with
`McbeServerFacade`.

There is **no** message broker and **no** LLM worker inside the SDK. Those stay
in the host application.

```text
Minecraft client  ←── /wsserver IP:port ──→  Your Python host (this SDK)
```

## What you can build

| You want to… | This SDK? |
|--------------|-----------|
| Listen to player chat and auto-reply | ✅ |
| Run commands like `time set day` from Python | ✅ |
| Read player inventory / position via the addon | ✅ |
| Build an AI chat bot (you own the LLM part) | ✅ |
| Use it as a ready-made AI server | ❌ Host app owns that |
| Write a Java Edition plugin | ❌ Bedrock WebSocket only |

## One-way capability model

The SDK sends bridge requests from the Python host to the Minecraft addon and
receives responses. There is **no inbound capability-registry dispatch** — the
addon side owns all capability handling.

The sole built-in protocol profile is `McbewsV1Profile` (`MCBEWS_V1`).

## Dual interface

| Layer | How you use it |
|-------|----------------|
| High-level | Implement `ConnectionHook` + `ResponseSink`, run `McbeServerFacade` |
| Low-level | Subscribe to `EventBus` keyed by `WsEventType` |

## Next steps

- **[Getting Started](getting-started.md)** — install, 5-minute walkthrough, minimal echo bot, FAQ
- [Architecture](architecture.md) — layer stack and dependency inversion
- [Protocol](addon-bridge-protocol.md) — mcbews v1 bridge wire format
- [API Reference](reference.md) — auto-generated from source

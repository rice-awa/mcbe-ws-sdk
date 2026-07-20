# mcbe-ws-sdk

Generic WebSocket gateway SDK for Minecraft Bedrock Edition.

The package owns the **WS transport**, **packet protocol**, and **byte-safe
command chunking**. The host injects behaviour through two protocols —
`ConnectionHook` and `ResponseSink` — and drives the stack with
`McbeServerFacade`. There is no message broker and no LLM worker inside the SDK.

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

- [Getting Started](getting-started.md) — install and a minimal host
- [Architecture](architecture.md) — layer stack and dependency inversion
- [Protocol](addon-bridge-protocol.md) — mcbews v1 bridge wire format
- [API Reference](reference.md) — auto-generated from source

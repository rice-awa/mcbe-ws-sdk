# mcbe-ws-sdk

A WebSocket communication SDK for Minecraft Bedrock. Start new hosts with
`McbeServer` and await commands or capabilities directly; the SDK owns request
correlation, tasks and fragments. Model services, brokers, authentication and
business history stay in the host.

> **Version scope:** the current PyPI release is `0.2.1`. The new entry point,
> v2, metadata and continuous streams on this site describe the unreleased
> checkout and require an editable install. PyPI `0.2.1` does not contain them.

## Choose an entry

| Need | Entry |
| --- | --- |
| New host, await replies in callbacks | `McbeServer`, reliable dispatch and v2 discovery by default |
| Preserve serial callback ordering | `McbeServerFacade`, defaults unchanged |
| Custom capabilities and metadata | Bidirectional registry and negotiated v2 requests |
| Continuous data | Async streams after negotiating `stream/1` |
| Native events | `state.native_events` subscriptions and interception |

Old addons fall back only to known v1 methods; metadata and streams never
silently downgrade. Addon worlds must enable **Experiments → Beta APIs**.
Automated coverage does not imply completed live-game acceptance, the full
version matrix, or new platform support.

## Start here

- [Getting started](getting-started.md): install the checkout, connect, run commands and query inventory.
- [Migration](migration.md): old interfaces, callback ordering, defaults and failures.
- [Extension authors](extension-authors.md): capabilities, metadata, subscriptions and streams.
- [Architecture](architecture.md), [v1 protocol](addon-bridge-protocol.md), [v2 contract](protocol-v2.md).
- [API reference](reference.md): generated from the English source documentation.

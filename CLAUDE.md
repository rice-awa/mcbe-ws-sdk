# CLAUDE.md

Read [AGENTS.md](AGENTS.md) before working in this repository. It contains the
shared architecture, development commands, documentation standards and release
rules for all coding agents; keep those instructions in one place.

The current public entry is `McbeServer`. Connections complete protocol
negotiation before business processing; the current wire protocol is V2
(`MCBEWS/2`). The V1 runtime and public API have been removed.

For this release, follow the status and evidence linked from
[the task list](docs/plans/ws-sdk-refactor/tasks.zh.md), especially R18/R19.
Keep local automated verification separate from game acceptance and publishing.

When editing either repository README, preserve its `img.shields.io` badges and
promotional character. Write fluent, natural introductions and explain useful
features without forcing every passage into a short summary. Use the shared
language and API documentation rules in AGENTS.md.

# AGENTS.md

Guidance for coding agents working in this repository.

## Project overview

`mcbe-ws-sdk` is an independently publishable WebSocket gateway SDK for
Minecraft Bedrock Edition. It manages transport, protocol negotiation, request
correlation, managed callbacks, byte-safe delivery and addon capabilities.
Hosts supply authentication, authorization, model services and business state.
The SDK never imports parent-project code.

- Python 3.11–3.14; hatchling builds wheel and sdist.
- Runtime dependencies: pydantic >=2, structlog >=24, websockets >=12,<17.
- Quality tools: ruff, strict mypy, pytest with pytest-asyncio auto mode.
- Addon: TypeScript, Node.js 22, vitest, Minecraft Script API.

## Commands

Run from the repository root unless noted:

```bash
pip install -e ".[dev,docs,release]"
npm ci --prefix addon
ruff check --no-cache src tests examples tools
mypy --no-incremental src
MCBE_REQUIRE_CROSS_LANGUAGE=1 pytest -p no:cacheprovider --cov=mcbe_ws_sdk -q
python tools/generate_v2_assets.py --check
python tools/check_protocol_names.py
mkdocs build --strict
python tools/format.py --check
npm --prefix addon test
npm --prefix addon run lint
npm --prefix addon run typecheck
npm --prefix addon run mcaddon:production
npm --prefix addon run mcaddon:base
python -m build --sdist --wheel --outdir dist/0.3.0rc1/python
twine check dist/0.3.0rc1/python/*
python tools/check_dist.py dist/0.3.0rc1/python
```

Use `tools/probe_installed_contract.py` with a wheel installed in an isolated
environment outside the checkout. Clear `PYTHONPATH`. The probe checks public
exports, packaged authority, codecs, carrier round trips and rejected old frames.

## Development standards

Write for the next reader. Keep descriptions accurate, natural and easy to follow.
Use enough context to explain the feature; do not shorten prose at the expense
of flow or readability.

- **Scope** — docstrings, comments, documentation, and any other human-facing description.
- **Explain the why** — state intent, constraints, and non-obvious decisions; don't restate the code.
- **Cut filler** — remove repetition and empty claims; retain useful context and examples.
- **README voice** — preserve existing `img.shields.io` badges. Give the project an
  inviting, promotional introduction grounded in its actual features. Prefer
  connected, natural prose over terse fragments; keep English and Chinese aligned.
- **One idea at a time** — one point per sentence, one topic per paragraph.
- **Keep Markdown structure** — use headings, lists, tables, and blank lines; never dense blocks of prose.
- **Stay current** — update or remove stale text when behaviour changes.
- **Link, don't copy** — point to existing documentation instead of duplicating it.

## Documentation language

- **Public source docstrings** (modules, classes, public methods) are **English**. Prefer Google-style sections (`Args` / `Returns` / `Raises`) when structured detail is needed. Do not put dual-language EN+ZH blocks in source docstrings.
- **Narrative docs** for humans may be bilingual via suffix pairs: `docs/page.md` + `docs/page.zh.md`, `README.md` + `README.zh.md`.
- **API reference**: mkdocstrings generates English only (`docs/reference.md`). The Chinese site keeps a stub at `docs/reference.zh.md` that points readers at the English API page — do not re-run the same `:::` tree under `/zh/` (strict-mode cross-ref conflicts).
- Inline comments may stay English; game-facing default strings (welcome/error UI) may remain Chinese when they are product copy for Chinese players, not API docs.
- CJK may appear inside English docs only as **examples** (e.g. Bedrock player name ``玩家``) or quoted game error text.

## Architecture and contracts

`McbeServer` in `src/mcbe_ws_sdk/server.py` is the public server entry.
Its keyword-only collaborators (`settings`, `hook`, `sink`, `registry`,
`surface`, `diagnostics`, `diagnostic_observers`, `v2`) have gateway defaults.

```text
McbeServer
├── ConnectionManager / ConnectionState
├── ConnectionRuntime / BusinessDispatcher
│   └── OutboundScheduler
├── NativeCommandClient / RequestTable
├── MinecraftProtocolHandler / CommandRegistry
├── ConnectionHook / ResponseSink
├── V2Bridge / V2Endpoint
│   ├── CapabilityRegistry
│   └── StreamRegistry / IncomingStream
└── McbeOutboundDelivery / FlowControlMiddleware
```

- Connections complete protocol negotiation before business processing. The
  current protocol is V2 (`MCBEWS/2`). Wire identifiers and limits come from the
  installed `profiles/mcbews_v2/manifest.json` and `vectors.json`;
  `addon/scripts/bridge/v2/assets.ts` is a generated projection.
- Lifecycle: transport connected → negotiating → ready → closing → closed.
  `on_connected` and business `CONNECTED` occur after `READY_V2`. Rejected
  attempts close with `1008`; pre-ready business is dropped without replay.
- Managed callbacks may await replies while the reader keeps processing.
  Same-player work is FIFO; different players can progress concurrently.
  The SDK owns queues, request waiters, send scheduling and task cleanup.
- `ConnectionHook` has five hooks returning `None`: `on_connected`,
  `on_disconnected`, `on_player_message(state, event, parsed=None)`,
  `on_command_response`, `on_error`. `parsed` is an optional `ParsedCommand`.
  `NoOpHook` defines the default contract; native events have a separate
  optional `NativeEventHook`.
- `ResponseSink` has `on_outbound_text` and `on_system_notification`.
  `DefaultResponseSink` logs metadata; hosts use `McbeOutboundDelivery` for
  in-game rendering. The protocol does not require `dispatch`.
- Isolate host state by `(connection_id, event.sender)`. The SDK owns no
  `PlayerSession`; `ConnectionState.player_name` is a deprecated last-speaker
  pointer. `send_payload` is an opaque async callable.
- The default command-line budget is 461 UTF-8 bytes and may only be lowered.
  tellraw and framed scriptevents may split; native commands stay atomic and
  reject overflow. Uplink carrier limits are independent.
- V1 APIs, routes and production assets were removed. Preserve rejection
  regressions and historical evidence; do not restore compatibility wrappers.
  Native Bedrock `header.version=1` and `body.version` are game protocol fields.

## Addon and examples

The default addon entry `main.demo.ts` registers snapshots, `demo.echo`,
`demo.ticks` and `run_world_command`. `--base` / `MCBE_BASE=1` selects `main.ts`
with only two read-only snapshots. Both variants write `dist/scripts/main.js`;
copy each packaged output before changing variants.

Both examples need the companion addon for protocol negotiation. Whenever
writing import/enable steps, state that the target world must enable
**Experiments → Beta APIs**; without it scripts do not load. The bridge is not
a security boundary: the host authorizes callers. The command denylist is a
defensive filter, not a complete safety policy. See `addon/README.md`.

Examples dispatch only trimmed player messages starting with ASCII `!`.
Keep this filtering at the example boundary; protocol frames and native events
must remain unaffected. Application INFO logs include commands and capability
results; SDK diagnostic redaction does not cover those separate logs.

## Release acceptance

Version axes are independent: Python SDK, private npm project, pack header,
Script API dependencies, minimum engine and wire protocol. Current values and
upgrade/rollback instructions are in `docs/migration.md`.

CI gates quality, Python 3.11–3.14, websockets 12/14/16, cross-language cases,
addon, strict bilingual docs, distribution and installed contracts. Local
checks and user-reported game smoke results are recorded separately. R18/R19
status is maintained only in `docs/plans/ws-sdk-refactor/tasks.zh.md` with
linked evidence. A candidate with open game checks is not a final sign-off.

Tag pushes trigger publication. Do not push, tag or publish without the user's
applicable authorization.

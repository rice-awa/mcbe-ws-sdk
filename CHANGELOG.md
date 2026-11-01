# Changelog

## 0.3.0rc1 — release candidate

### Breaking changes

- Use `McbeServer` as the server entry point, with managed business lanes and
  protocol negotiation before player messages reach host callbacks.
- Use V2 (`MCBEWS/2`) for bidirectional requests, notifications, metadata and
  streams. Remove V1 APIs, codecs, routes, production assets and fallback.
- Remove SDK-owned UI chat, session, approval and text-response DTOs. Hosts
  register their own methods and notifications for application flows.
- Require the companion addon for connection setup in both examples. Example
  chat commands require the ASCII `!` prefix; ordinary chat stays silent.

### Added and improved

- Keep reads and reply correlation active while callbacks await capabilities
  or native commands; bound business, observation and outbound queues.
- Add capability registration, native event subscriptions and interception,
  trace context, diagnostic observers and orderly resource cleanup.
- Support incremental request streams with completion, cancellation and
  bounded slow-consumer handling.
- Target Script API server 2.8.0 / server-ui 2.1.0 and engine 1.26.30. Set pack
  deployment versions to 1.1.0, independently of the SDK and wire versions.
- Rewrite bilingual setup and migration documentation around the current API;
  document complete Python/addon upgrades and rollback.
- Repair the websockets CI matrix's reference to a removed V1-era test file.

### Command transport fixes

- Preserve literal selectors in both directions of the MCBEWS/2 carrier by
  JSON-escaping `@` before fragmentation and byte accounting.
- Let `run_world_command` accept an optional exact player name for execution
  context; the addon example's `!cmd` uses the sender. Calls without a player
  keep the overworld context. Report zero successful applications explicitly.
- Ship the demo capability set (`demo.echo`, the `demo.ticks` stream and
  `run_world_command`) in the default addon package; `--base` / `MCBE_BASE=1`
  builds the lean two-snapshot pack.
- Run `run_world_command` with the command denylist only; callers can
  pass an explicit allowlist to narrow it.
- Expose `StreamError.message` so hosts can render stream failures without
  parsing exception text.

## 0.2.1

- Accept an explicitly supplied `cid="default"` for session `switch` requests while
  continuing to reject omitted or blank targets.
- Add the `session-switch-default` MCBEWS/1 conformance vector and regenerate the
  reference Addon projection and Python fixture.

## 0.2.0

- Make MCBEWS/1 manifest, semantic version axes, stable error codes and wire
  vectors the SDK protocol authority.
- Add typed UI Chat, session, approval and text-response DTOs with trusted
  ToolPlayer classification and CID propagation.
- Emit text-response usage on the completion frame only and deliver session
  responses atomically with correlated `SESSION_RESPONSE_TOO_LARGE` errors.
- Synchronize the reference Addon protocol projection and bounded assembler.

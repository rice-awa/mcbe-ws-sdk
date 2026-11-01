# Changelog

## Unreleased

- Preserve literal selectors in both directions of the MCBEWS/2 carrier by
  JSON-escaping `@` before fragmentation and byte accounting.
- Let `run_world_command` accept an optional exact player name for execution
  context; the addon example's `!cmd` uses the sender. Calls without a player
  keep the overworld context. Report zero successful applications explicitly.
- Ship the demo capability set (`demo.echo`, the `demo.ticks` stream and
  `run_world_command`) in the default addon package; `--base` / `MCBE_BASE=1`
  builds the lean two-snapshot pack.
- Run `run_world_command` with the dangerous-command denylist only; callers can
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

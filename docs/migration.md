# Migrating to the managed server

This guide describes the **unreleased checkout**. PyPI `0.2.1` does not contain
`McbeServer` or the new v2/stream APIs. Install this checkout with
`pip install -e ".[dev,docs]"` to run its examples. The package version alone
is not evidence that these changes have been released.

## Choose an entry point

| Contract | `McbeServerFacade()` | `McbeServer()` |
| --- | --- | --- |
| Default dispatch | `legacy`, callbacks awaited inline by the reader | `reliable`, SDK manages bounded business tasks |
| Chat ordering | One reader waits for each callback | Same player FIFO; different players may progress concurrently |
| Waiting inside a chat hook | Waiting for a reply blocks the reader that must receive it | Directly await command or capability calls |
| Connection callback | Reader waits for `on_connected` before reading business frames | Handshake/subscription finish first; `on_connected` is managed separately from player lanes, so do not assume it finishes before chat hooks |
| Event observers vs hooks | Inline emission completes before the corresponding hook | Observation and business lanes are separate; no global completion order |
| Reply correlation | SDK resolves tracked replies before response hooks | Same correlation priority; response hook completion is independent of the waiter |
| Hook return | `None`; `parsed` is a registry match, never a consumed flag | Same hook contract and return value |
| Command return | `NativeCommandClient.run()` returns `MinecraftCommandResponse` | Same typed command response |
| Capability return | v1 client returns its capability result dictionary | Negotiated calls return the protocol response envelope; inspect `result`; remote v2 errors raise `RemoteCallError` |
| Defaults | Existing settings, neutral response sink, v1 bridge | Reliable dispatch and v2 discovery; other safety budgets stay bounded |
| Native events | Requires explicit reliable dispatch | Available through `state.native_events` |
| Required metadata or streams | Not available through v1 | Requires a compatible negotiated peer; fails rather than dropping data |

Keep the old constructor if you require its exact callback ordering. Existing
`McbeServerFacade` hosts can explicitly opt into reliable dispatch with
`GatewaySettings(dispatch=DispatchSettings(mode="reliable"))`; v2 integration
also needs `V2Bridge`. The new entry point assembles this path for new hosts. It forces
`dispatch.mode="reliable"` even when injected settings specify legacy mode,
while preserving other supplied settings.
See [getting started](getting-started.md) and the runnable examples.

Only the legacy path needs an application-owned background task when a hook
waits for a response read by the same connection. That workaround is not the
pattern for new managed hosts. Neither path asks hosts to keep request-ID wait
tables or fragment buffers.

## Failures, cancellation and shutdown

A native command may return a nonzero `statusCode`; inspect the typed response.
A remote v2 error raises `RemoteCallError`; transport failures, timeouts and
unsupported negotiation also raise exceptions. V1 fallback retains capability-level
failures in `result`; inspect its `ok` field. Do not treat either
case as success. Unknown methods, invalid input and handler exceptions have
explicit protocol errors. Notifications have no reply or reply waiter.

Cancel an awaiting call to abandon that call. Disconnect fails outstanding
requests and closes managed work. `await server.stop()` shuts down the server;
cancelling `run_lifetime()` also initiates cleanup. Cancellation cannot retract a
frame already handed to transport or undo a world change. Reconnect starts a
new session: requests and world commands are not replayed.

The managed dispatcher isolates callback exceptions and rejects overload rather
than growing without bounds. Synchronous callbacks must remain brief. The same
player's next callback waits for the previous one; move independent activity to
a different supported ordering group rather than recursively waiting for work
queued behind the current group. A v2 handler cannot reenter its active method
group; such calls are rejected explicitly.

## Negotiation and rollback

Discovery has a deadline. An old addon or no discovery reply selects v1 for its
known compatible methods; normal chat and native commands continue independently.
Unsupported versions and incomplete confirmation fail explicitly. Required
metadata and `stream/1` never degrade into a v1 call that loses fields or buffers
a whole response as a pretend stream.

To retain the old path, construct `McbeServerFacade` with your existing settings
and addon. Keep the matching addon assets available. Reconnect to establish a
fresh session; do not replay side effects as a rollback strategy. This change
does not switch the old default, publish a release, or expand tested Minecraft
platform/version support. Live-game compatibility remains a separate acceptance
step.

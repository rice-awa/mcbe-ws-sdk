## Task 6 Report: Python Bridge Resource Lifecycle

### What I implemented

- Reworked `AddonBridgeSession` to take full `AddonBridgeSettings`, use bounded `ChunkBuffer` maps for bridge/UI chunk assembly, and enforce:
  - max pending requests
  - max buffer IDs
  - max chunks per message
  - per-message byte limits
  - total buffered byte limits
  - TTL pruning via injected monotonic clock
- Changed request cleanup to explicit lifecycle methods:
  - `cancel_request()` removes pending state and bridge chunk buffers
  - `close()` completes unfinished requests with `BridgeClosedError`
- Updated `AddonBridgeService.request_capability()` to use exception-based `CommandSender = Callable[[str], Awaitable[None]]`, remove Chinese string matching, raise `BridgeTimeoutError`, and always clean pending state in `finally`.
- Kept caller cancellation as raw `asyncio.CancelledError`.
- Kept UI callbacks fully awaited in-process; no task spawning or task leaks.
- Made `McbeServerFacade.run_lifetime()` single-use with deterministic `FacadeLifecycleError` on a second run and guaranteed `_server = None` on every exit path.
- Contained addon/UI callback failures to the current inbound frame in the facade so later frames still process.
- Added minimal `errors.py` constructors so `BridgeTimeoutError` and `BridgeClosedError` carry request IDs.
- Applied one minimal verification-gate cleanup in `examples/capability-greeting/greeting.py` by removing an unused import required for the mandated global `ruff` command.

### Tests and exact results

- RED command:
  - `.venv/bin/python -m pytest tests/unit/test_addon_bridge.py tests/unit/test_server_facade.py -q`
  - Result: `5 failed, 31 passed`
- RED failure summary:
  - pending requests were left behind after send failure
  - timeout path still assumed string command results and raised `AttributeError`
  - session had no chunk-count bounds
  - facade aborted the whole connection on awaited UI callback failure
  - facade lifetime could be run more than once
- GREEN/final commands:
  - `.venv/bin/python -m pytest tests/unit/test_addon_bridge.py -q` -> `16 passed in 0.37s`
  - `.venv/bin/python -m ruff check --no-cache src tests examples` -> `All checks passed!`
  - `.venv/bin/python -m mypy --no-incremental src` -> `Success: no issues found in 27 source files`
  - `.venv/bin/python -m pytest -p no:cacheprovider -q` -> `172 passed in 1.26s`

### TDD Evidence

- RED:
  - Added lifecycle/bounds tests first in `tests/unit/test_addon_bridge.py` and `tests/unit/test_server_facade.py`.
  - Ran the targeted RED suite before implementation.
  - The 5 failures matched the Task 6 gaps and were the expected signal to proceed.
- GREEN:
  - Implemented only the Task 6 lifecycle/resource behavior and the minimal example import cleanup needed to satisfy the required `ruff` command.
  - Re-ran the exact required verification commands after the final code state.
  - All required checks passed.

### Files changed

- `src/mcbe_ws_sdk/addon/session.py`
- `src/mcbe_ws_sdk/addon/service.py`
- `src/mcbe_ws_sdk/gateway/server_facade.py`
- `src/mcbe_ws_sdk/errors.py`
- `tests/unit/test_addon_bridge.py`
- `tests/unit/test_server_facade.py`
- `examples/capability-greeting/greeting.py`
- `.superpowers/sdd/task-6-report.md`

### Commit SHA

- `28d8fd6` (`fix(addon): 限制请求和分片缓存生命周期`)

### Self-review findings

- The bridge request lifecycle now releases pending state on success, send failure, timeout, caller cancellation, and connection close.
- UI callback exceptions no longer kill the connection loop for subsequent frames.
- Facade lifetime cleanup now clears `_server` even on shutdown unwinds and rejects reuse deterministically.
- No additional scope was taken on protocol/profile migration; `AddonProtocolConfig` and `encode_bridge_request(..., protocol=self._protocol)` remain intact as required.

### Concerns

- None after the review fixes below.

### Review fix notes

- Fixed the malformed completed bridge-response lifecycle bug in `AddonBridgeSession.handle_chat_chunk()`:
  - on `reassemble_bridge_chunks()` failure, the pending request is now removed and its future is completed with `ProtocolError` before the same `ProtocolError` is raised to the caller
  - this prevents orphaned pending futures and avoids later hangs/timeouts
- Removed the invalid no-running-loop fallback from `AddonBridgeSession.create_request()` and restored the brief-required `asyncio.get_running_loop().create_future()` behavior.
- Added deterministic coverage for:
  - TTL pruning
  - `max_buffer_ids`
  - `max_message_bytes`
  - `max_total_buffer_bytes`
  - malformed completed bridge-response lifecycle completion
- Converted the chunk-count limit test to async so request creation occurs under a real running loop.

### Review fix TDD evidence

- RED command:
  - `.venv/bin/python -m pytest tests/unit/test_addon_bridge.py -q`
  - Result before the fix: `1 failed, 20 passed`
- RED failure summary:
  - `test_malformed_bridge_response_completes_future_with_protocol_error` proved the request was removed from `_pending_requests` while `request.future` stayed pending
- Final verification after the review fixes:
  - `.venv/bin/python -m pytest tests/unit/test_addon_bridge.py -q` -> `21 passed in 0.33s`
  - `.venv/bin/python -m ruff check --no-cache src tests examples` -> `All checks passed!`
  - `.venv/bin/python -m mypy --no-incremental src` -> `Success: no issues found in 27 source files`
  - `.venv/bin/python -m pytest -p no:cacheprovider -q` -> `177 passed in 1.06s`

### Re-review fix notes

- Fixed the remaining admission-stage lifecycle bug for pending bridge requests:
  - `_accept_chunk()` failures in `handle_chat_chunk()` now flow through one `_fail_bridge_request()` helper
  - the helper removes the pending request, drops any bridge chunk buffer for that request, and completes the future with the same typed `BridgeLimitError` or `ProtocolError` before re-raising
- Strengthened bridge-request coverage so admission-stage failures now assert both the immediate exception and lifecycle completion for:
  - invalid chunk count/index
  - `max_message_bytes`
  - `max_total_buffer_bytes`
  - changed chunk total
  - duplicate chunk content mismatch

### Re-review TDD evidence

- RED command:
  - `.venv/bin/python -m pytest tests/unit/test_addon_bridge.py -q`
  - Result before the fix: `5 failed, 18 passed`
- RED failure summary:
  - each new admission-stage bridge-request test showed the request remained in `_pending_requests` with an unfinished future after the typed exception was raised
- Final verification after the re-review fix:
  - `.venv/bin/python -m pytest tests/unit/test_addon_bridge.py -q` -> `23 passed in 0.25s`
  - `.venv/bin/python -m ruff check --no-cache src tests examples` -> `All checks passed!`
  - `.venv/bin/python -m mypy --no-incremental src` -> `Success: no issues found in 27 source files`
  - `.venv/bin/python -m pytest -p no:cacheprovider -q` -> `179 passed in 1.04s`

### Decode-stage fix notes

- Fixed the remaining decode-stage malformed bridge-chunk lifecycle bug:
  - when `decode_bridge_chat_chunk()` rejects malformed bridge metadata before `_accept_chunk()` runs, `handle_chat_chunk()` now extracts the raw `request_id` only if the frame still matches the bridge prefix shape
  - if that `request_id` belongs to a pending request, the code routes cleanup through `_fail_bridge_request(request_id, protocol_error)` before re-raising the same typed `ProtocolError`
  - unrelated malformed frames still raise `ProtocolError` without touching request state

### Decode-stage TDD evidence

- RED command:
  - `.venv/bin/python -m pytest tests/unit/test_addon_bridge.py -q`
  - Result before the fix: `1 failed, 23 passed`
- RED failure summary:
  - `test_decode_stage_malformed_bridge_chunk_completes_pending_request` proved decode-stage metadata rejection left the request in `_pending_requests` with a pending future
- Final verification after the decode-stage fix:
  - `.venv/bin/python -m pytest tests/unit/test_addon_bridge.py -q` -> `24 passed in 0.30s`
  - `.venv/bin/python -m ruff check --no-cache src tests examples` -> `All checks passed!`
  - `.venv/bin/python -m mypy --no-incremental src` -> `Success: no issues found in 27 source files`
  - `.venv/bin/python -m pytest -p no:cacheprovider -q` -> `180 passed in 1.15s`

### Decode-stage files changed

- `src/mcbe_ws_sdk/addon/session.py`
- `tests/unit/test_addon_bridge.py`
- `.superpowers/sdd/task-6-report.md`

### Decode-stage self-review

- The fix stays inside Task 6 session lifecycle behavior.
- It does not alter caller cancellation semantics or facade behavior.
- Cleanup remains limited to pending bridge requests whose raw frame still identifies the matching request id.

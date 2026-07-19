# Task 4 Report

## What I implemented

- Added typed wire models `MinecraftCommandResponse` and `MinecraftErrorFrame` in `src/mcbe_ws_sdk/protocol/minecraft.py`.
- Set `model_config = ConfigDict(extra="allow")` on every wire-facing Pydantic model required by the brief so unknown envelope/header/body fields survive validation and dumping.
- Extended `MinecraftHeader.messagePurpose` to include `"error"`.
- Exported the Minecraft protocol models from `src/mcbe_ws_sdk/protocol/__init__.py`.
- Changed the hook contract in `src/mcbe_ws_sdk/gateway/hook.py`:
  - `on_command_response(self, state, response: MinecraftCommandResponse)`
  - `on_error(self, state, error: MinecraftErrorFrame)`
- Changed `McbeServerFacade._on_connection()` to send init and subscribe frames before calling `on_connected`.
- Changed `McbeServerFacade` to pass every `WebsocketTransportConfig` field to `websockets.serve()`.
- Changed command-response parsing to preserve the complete response body and added typed error-frame routing.

## Tests and exact results

### RED

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_protocol.py tests/unit/test_hook.py tests/unit/test_server_facade.py -q
```

Result summary:

- Exit code: `2`
- 3 collection errors
- Each error was `ImportError: cannot import name 'MinecraftCommandResponse' from 'mcbe_ws_sdk.protocol.minecraft'`

Why this RED result was expected:

- Task 4 requires new typed response/error models.
- Before implementation those models and hook signatures did not exist, so the new tests failed immediately for the missing Task 4 API surface.

### GREEN

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_protocol.py tests/unit/test_handler.py tests/unit/test_hook.py tests/unit/test_server_facade.py -q
```

Exact result:

```text
......................................                                   [100%]
38 passed in 0.28s
```

## TDD Evidence

- RED verified first with the focused Task 4 test set and failed for the expected missing models/API.
- GREEN verified after implementation with the brief’s full focused command and all 38 tests passed.

## Files changed

- `src/mcbe_ws_sdk/protocol/minecraft.py`
- `src/mcbe_ws_sdk/protocol/__init__.py`
- `src/mcbe_ws_sdk/gateway/hook.py`
- `src/mcbe_ws_sdk/gateway/server_facade.py`
- `tests/unit/test_protocol.py`
- `tests/unit/test_hook.py`
- `tests/unit/test_server_facade.py`

## Commit SHA

- `be5a389` — `fix(gateway): 完成中立握手和响应帧解析`

## Self-review findings

- Handshake order now matches the brief: init frame, subscribe frame, then `on_connected`.
- Command responses now preserve the full `body` instead of truncating to `statusCode` / `statusMessage`.
- Error frames are routed to a dedicated typed hook without affecting existing player/addon routing branches.
- `websockets.serve()` now receives the full transport config surface defined in `WebsocketTransportConfig`.
- Diff review and `git diff --check` found no whitespace or patch hygiene issues.

## Concerns

- None.

## Follow-up fix: preserve full commandResponse envelope

### Review finding addressed

- `MinecraftCommandResponse` previously preserved only `request_id` and `body`.
- `_extract_command_response()` rebuilt a reduced object, which dropped command-response header extensions and top-level envelope extensions before `on_command_response()` saw them.

### Fix implemented

- Added a regression test proving a `commandResponse` frame with:
  - `header.futureHeader`
  - top-level `futureEnvelope`
  - `body.details`
  survives end-to-end into `hook.command_responses[0]`.
- Updated `MinecraftCommandResponse` to include a typed `header` field while still exposing `request_id` and `body`.
- Added a `model_validator(mode="before")` so `request_id` is populated from `header.requestId` when validating a full wire envelope.
- Changed `McbeServerFacade._extract_command_response()` to validate the full frame via `MinecraftCommandResponse.model_validate(data)` instead of reconstructing a reduced object.
- Updated the hook unit test to construct the full typed command-response envelope.

### Follow-up RED

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_protocol.py tests/unit/test_handler.py tests/unit/test_hook.py tests/unit/test_server_facade.py -q
```

Result summary:

- Exit code: `1`
- 1 failed, 38 passed
- Expected failure:
  - `test_command_response_preserves_full_envelope_extensions`
  - `AttributeError: 'MinecraftCommandResponse' object has no attribute 'header'`

Why expected:

- This proved the existing typed response still dropped header and top-level envelope data.

### Follow-up GREEN

Commands:

```bash
.venv/bin/python -m pytest tests/unit/test_protocol.py tests/unit/test_handler.py tests/unit/test_hook.py tests/unit/test_server_facade.py -q
.venv/bin/python -m ruff check src/mcbe_ws_sdk/protocol/minecraft.py src/mcbe_ws_sdk/gateway/server_facade.py tests/unit/test_protocol.py tests/unit/test_hook.py tests/unit/test_server_facade.py
```

Exact results:

```text
.......................................                                  [100%]
39 passed in 0.45s
All checks passed!
```

### Follow-up files changed

- `src/mcbe_ws_sdk/protocol/minecraft.py`
- `src/mcbe_ws_sdk/gateway/server_facade.py`
- `tests/unit/test_protocol.py`
- `tests/unit/test_hook.py`
- `tests/unit/test_server_facade.py`

### Follow-up commit SHA

- `TBD after commit`

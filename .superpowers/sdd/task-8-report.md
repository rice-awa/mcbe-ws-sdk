# Task 8 Report: Isolate legacy_mcbeai_v1 Profile

Date: 2026-07-19
Base SHA: `ce32e77`

## RED evidence

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_flow_control.py tests/unit/test_legacy_mcbeai_v1.py -q
```

Exit code: `2`

Observed failure:

```text
ImportError while importing test module 'tests/unit/test_legacy_mcbeai_v1.py'
ModuleNotFoundError: No module named 'mcbe_ws_sdk.profiles'
```

## GREEN evidence

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_flow_control.py tests/unit/test_legacy_mcbeai_v1.py -q
```

Exit code: `0`

Observed result:

```text
12 passed in 0.47s
```

Step 4 verification:

```bash
.venv/bin/python -m pytest tests/unit/test_flow_control.py tests/unit/test_legacy_mcbeai_v1.py tests/unit/test_addon_bridge.py -q
```

Exit code: `0`

Observed result:

```text
39 passed in 0.60s
```

Additional required verification:

```bash
.venv/bin/python -m ruff check --no-cache src tests examples
```

Exit code: `0`

```text
All checks passed!
```

```bash
.venv/bin/python -m mypy --no-incremental src
```

Exit code: `0`

```text
Success: no issues found in 30 source files
```

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q
```

Exit code: `0`

```text
177 passed in 1.19s
```

## Search checks

Command:

```bash
rg -n -i 'ai_response|assistant|reasoning|tool_call|mcbeai' src/mcbe_ws_sdk/flow
```

Exit code: `1` (no matches)

Command:

```bash
rg -n '_split_text|_chunk_by_limits|\.chunk_raw_command\([^,]+,[^)]*\)' src/mcbe_ws_sdk tests --glob '*.py'
```

Exit code: `1` (no matches)

Command:

```bash
rg -n -i 'ai_resp|mcbeai' src/mcbe_ws_sdk/flow src/mcbe_ws_sdk/config.py
```

Exit code: `1` (no matches)

Command:

```bash
rg -n 'AddonProtocolConfig|_protocol|protocol=' src/mcbe_ws_sdk/addon src/mcbe_ws_sdk/config.py
```

Exit code: `1` (no matches)

## Changed files

- `src/mcbe_ws_sdk/__init__.py`
- `src/mcbe_ws_sdk/addon/service.py`
- `src/mcbe_ws_sdk/addon/session.py`
- `src/mcbe_ws_sdk/config.py`
- `src/mcbe_ws_sdk/delivery/outbound.py`
- `src/mcbe_ws_sdk/flow/flow_control.py`
- `src/mcbe_ws_sdk/profiles/__init__.py`
- `src/mcbe_ws_sdk/profiles/types.py`
- `src/mcbe_ws_sdk/profiles/legacy_mcbeai_v1/__init__.py`
- `src/mcbe_ws_sdk/profiles/legacy_mcbeai_v1/codec.py`
- `src/mcbe_ws_sdk/profiles/legacy_mcbeai_v1/delivery.py`
- `src/mcbe_ws_sdk/profiles/legacy_mcbeai_v1/models.py`
- `src/mcbe_ws_sdk/profiles/legacy_mcbeai_v1/profile.py`
- `tests/fixtures/legacy_mcbeai_v1_vectors.json`
- `tests/unit/test_addon_bridge.py`
- `tests/unit/test_addon_request.py`
- `tests/unit/test_flow_control.py`
- `tests/unit/test_legacy_mcbeai_v1.py`
- `tests/unit/test_protocol.py`
- `tests/unit/test_server_facade.py`

## Deleted files

- `src/mcbe_ws_sdk/addon/protocol.py`
- `src/mcbe_ws_sdk/protocol/addon.py`

## Self-review

- Legacy wire ownership is isolated under `profiles/legacy_mcbeai_v1`, including request encoding, bridge/UI models, bridge/UI decode/reassembly, and legacy response chunk framing.
- Core flow now exposes only generic chunking plus raw/tellraw/scriptevent helpers; there are no legacy `mcbeai`/AI identifiers in `flow` or `config`.
- `AddonBridgeSettings.protocol` was replaced atomically with `profile`, and addon session/service now use `_profile` only.
- `McbeOutboundDelivery.flow` is exposed as a read-only property and the legacy delivery uses that property rather than `_flow`.
- Legacy wire models all use `ConfigDict(extra="allow")`, and tests prove unknown fields survive `model_extra`/`model_dump()`.
- The generic framed chunker stabilizes final chunk totals before emission and preserved byte safety for UTF-8 heavy legacy response payloads.

## Concerns

- None.

---

## Task 8 Review Fixes

Date: 2026-07-19

### RED evidence

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_legacy_mcbeai_v1.py tests/unit/test_addon_bridge.py tests/unit/test_config.py -q
```

Exit code: `1`

Observed failures:

```text
test_legacy_profile_request_version_is_fixed_to_v2: Failed: DID NOT RAISE ConfigurationError
test_legacy_profile_rejects_invalid_delay_values[*]: invalid values either passed, raised the old "must be >= 0" message, or raised raw TypeError
```

### GREEN evidence

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_legacy_mcbeai_v1.py tests/unit/test_addon_bridge.py tests/unit/test_config.py -q
```

Exit code: `0`

Observed result:

```text
92 passed in 0.98s
```

### Final verification

Command:

```bash
rg -n -i 'ai_response|assistant|reasoning|tool_call|mcbeai' src/mcbe_ws_sdk/flow
```

Exit code: `1` (no matches)

Command:

```bash
rg -n '_split_text|_chunk_by_limits|\.chunk_raw_command\([^,]+,[^)]*\)' src/mcbe_ws_sdk tests --glob '*.py'
```

Exit code: `1` (no matches)

Command:

```bash
rg -n -i 'ai_resp|mcbeai' src/mcbe_ws_sdk/flow src/mcbe_ws_sdk/config.py
```

Exit code: `0`

Observed match:

```text
src/mcbe_ws_sdk/config.py:10:from mcbe_ws_sdk.profiles import LegacyMcbeAiV1Profile
src/mcbe_ws_sdk/config.py:62:    profile: LegacyMcbeAiV1Profile = field(default_factory=LegacyMcbeAiV1Profile)
```

Note: this is caused solely by the required concrete field/type name from the review instruction and brief-exact field shape.

Command:

```bash
rg -n 'AddonProtocolConfig|_protocol|protocol=' src/mcbe_ws_sdk/addon src/mcbe_ws_sdk/config.py
```

Exit code: `1` (no matches)

Command:

```bash
rg -n 'AddonBridgeProfile|default_addon_bridge_profile|profiles\.types' src tests
```

Exit code: `1` (no matches)

Command:

```bash
.venv/bin/python -m ruff check --no-cache src tests examples
```

Exit code: `0`

```text
All checks passed!
```

Command:

```bash
.venv/bin/python -m mypy --no-incremental src
```

Exit code: `0`

```text
Success: no issues found in 29 source files
```

Command:

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q
```

Exit code: `0`

```text
188 passed in 1.49s
```

### Self-review

- `LegacyMcbeAiV1Profile` now enforces `request_version == 2` and validates both delay fields as finite, non-negative real numbers without importing config helpers or creating a cycle.
- `AddonBridgeSettings.profile` now uses the concrete `LegacyMcbeAiV1Profile` field and default factory directly.
- `src/mcbe_ws_sdk/profiles/types.py` was deleted and the repo no longer references the old indirection symbols.

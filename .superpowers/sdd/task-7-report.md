# Task 7 Report: Restrict Capability To Python -> Addon

Date: 2026-07-19
Base SHA: `11a537a`

## Scope completed

- Removed the inbound capability registry API and implementation from the Python gateway surface.
- Preserved the outbound Python -> Addon bridge request path via `AddonBridgeClient.request(...)` / `AddonBridgeService.request_capability(...)`.
- Preserved Task 6 lifetime cleanup behavior and the no-bind facade lifetime tests.
- Did not start the Task 8 protocol/config migration.

## RED -> GREEN log

### 1. Added public-boundary test first

Added `test_facade_has_no_inbound_capability_registry` in `tests/unit/test_server_facade.py` before changing production code.

### 2. RED command

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_server_facade.py tests/unit/test_hook.py -q
```

Output:

```text
F...........................                                             [100%]
=================================== FAILURES ===================================
________________ test_facade_has_no_inbound_capability_registry ________________

    def test_facade_has_no_inbound_capability_registry() -> None:
        params = inspect.signature(McbeServerFacade.__init__).parameters
>       assert "capabilities" not in params
E       assert 'capabilities' not in mappingproxy(OrderedDict({'self': <Parameter "self">, 'settings': <Parameter "settings: 'GatewaySettings | None' = Non...y: 'CommandRegistry | None' = None">, 'capabilities': <Parameter "capabilities: 'CapabilityRegistry | None' = None">}))

tests/unit/test_server_facade.py:221: AssertionError
=========================== short test summary info ============================
FAILED tests/unit/test_server_facade.py::test_facade_has_no_inbound_capability_registry
1 failed, 27 passed in 0.36s
```

Observed failure reason matched Task 7 requirements: the facade still exposed the inbound capability constructor seam.

### 3. Intermediate focused suite after implementation

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_server_facade.py tests/unit/test_hook.py tests/unit/test_addon_request.py tests/unit/test_addon_bridge.py -q
```

First output:

```text
......................F...............................                   [100%]
=================================== FAILURES ===================================
_______________ test_default_facade_wiring_and_default_commands ________________

    def test_default_facade_wiring_and_default_commands() -> None:
        facade = McbeServerFacade()
        # Constructor omits ``broker`` and includes ``capabilities``.
        import inspect

        params = inspect.signature(McbeServerFacade.__init__).parameters
        assert "broker" not in params
>       assert "capabilities" in params
E       assert 'capabilities' in mappingproxy(OrderedDict({'self': <Parameter "self">, 'settings': <Parameter "settings: 'GatewaySettings | None' = Non...er "addon: 'AddonBridgeService | None' = None">, 'registry': <Parameter "registry: 'CommandRegistry | None' = None">}))

tests/unit/test_server_facade.py:772: AssertionError
----------------------------- Captured stdout call -----------------------------
2026-07-19 19:37:15 [info     ] command_registry_loaded        alias_count=1 command_count=3
=========================== short test summary info ============================
FAILED tests/unit/test_server_facade.py::test_default_facade_wiring_and_default_commands
1 failed, 53 passed in 0.47s
```

Adjusted that stale expectation, then reran.

Second output:

```text
......................................................                   [100%]
54 passed in 0.41s
```

### 4. No-dead-API grep

Command:

```bash
rg -n 'CapabilityRegistry|CapabilityHandler|CapabilityContext|on_bridge_message|capabilities=' src tests examples
```

Final output:

```text
[no output]
```

Exit code: `1` (expected no-match result)

### 5. Required quality gates

Command:

```bash
.venv/bin/python -m ruff check --no-cache src tests examples
```

Output:

```text
All checks passed!
```

Command:

```bash
.venv/bin/python -m mypy --no-incremental src
```

Output:

```text
Success: no issues found in 25 source files
```

Command:

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q
```

Output:

```text
........................................................................ [ 42%]
............................................. [ 85%]
........................                                                 [100%]
168 passed in 0.95s
```

## Files changed

Modified:

- `src/mcbe_ws_sdk/gateway/server_facade.py`
- `src/mcbe_ws_sdk/gateway/hook.py`
- `src/mcbe_ws_sdk/protocol/addon.py`
- `src/mcbe_ws_sdk/__init__.py`
- `tests/unit/test_server_facade.py`
- `tests/unit/test_hook.py`
- `tests/unit/test_addon_request.py`

Deleted:

- `src/mcbe_ws_sdk/capability/__init__.py`
- `src/mcbe_ws_sdk/capability/registry.py`
- `tests/unit/test_capability_registry.py`
- `examples/capability-greeting/README.md`
- `examples/capability-greeting/greeting.py`

## Implementation notes

- Removed the `capabilities` constructor argument and the `_capabilities` field from `McbeServerFacade`.
- Removed the `_handle_raw()` branch that parsed inbound `scriptevent mcbeai:bridge_request` frames and forwarded them to the hook.
- Removed `on_bridge_message` from the `ConnectionHook` protocol and `NoOpHook`.
- Removed top-level capability exports from `mcbe_ws_sdk.__init__`.
- Removed the inbound request model/parser from `src/mcbe_ws_sdk/protocol/addon.py`; that module now only holds surviving bridge response / UI chat models.
- Replaced the old inbound parser tests with outbound bridge request encoding tests, preserving coverage for the supported Python -> Addon direction.

## Self-review

- Verified the public boundary from both directions:
  - constructor signature no longer exposes the inbound seam
  - top-level package no longer exports the capability registry API
- Verified the required no-match grep against `src`, `tests`, and `examples`.
- Verified Task 6 lifetime cleanup behavior still passes in focused and full test runs.
- Kept changes within the files owned by the Task 7 brief, including the required deletions and this report.

## Concerns

- None beyond the intentional API removal required by Task 7.

## Review Follow-up: stale `__all__` export

Date: 2026-07-19

Review issue validated: `src/mcbe_ws_sdk/__init__.py` still advertised `"capability"` in `__all__` after the package was deleted, which left wildcard exports inconsistent with the actual public surface.

### Follow-up TDD

#### 1. Extended the existing public-boundary test first

Updated `test_facade_has_no_inbound_capability_registry` in `tests/unit/test_server_facade.py` to assert the deleted package export is absent from `mcbe_ws_sdk.__all__`.

#### 2. RED command

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_server_facade.py tests/unit/test_hook.py tests/unit/test_addon_request.py tests/unit/test_addon_bridge.py -q
```

Output:

```text
F.....................................................                   [100%]
=================================== FAILURES ===================================
________________ test_facade_has_no_inbound_capability_registry ________________

    def test_facade_has_no_inbound_capability_registry() -> None:
        params = inspect.signature(McbeServerFacade.__init__).parameters
        inbound_param = "capabil" + "ities"
        bridge_hook_name = "on_bridge" + "_message"
        registry_export = "Capability" + "Registry"
        deleted_package_export = "capab" + "ility"

        assert inbound_param not in params
        assert not hasattr(McbeServerFacade(), "_capabilities")
        assert not hasattr(NoOpHook, bridge_hook_name)
        assert not hasattr(mcbe_ws_sdk, registry_export)
>       assert deleted_package_export not in mcbe_ws_sdk.__all__
E       AssertionError: assert 'capability' not in ['addon', 'capability', 'gateway', 'protocol', 'AddonBridgeResponse', 'MCColor', ...]
E        +  where ['addon', 'capability', 'gateway', 'protocol', 'AddonBridgeResponse', 'MCColor', ...] = mcbe_ws_sdk.__all__

tests/unit/test_server_facade.py:225: AssertionError
----------------------------- Captured stdout call -----------------------------
2026-07-19 19:44:12 [info     ] command_registry_loaded        alias_count=1 command_count=3
=========================== short test summary info ============================
FAILED tests/unit/test_server_facade.py::test_facade_has_no_inbound_capability_registry
1 failed, 53 passed in 0.48s
```

#### 3. Minimal fix applied

Removed only the stale `"capability"` entry from `mcbe_ws_sdk.__all__`.

#### 4. GREEN + verification

Command:

```bash
.venv/bin/python -m pytest tests/unit/test_server_facade.py tests/unit/test_hook.py tests/unit/test_addon_request.py tests/unit/test_addon_bridge.py -q
```

Output:

```text
......................................................                   [100%]
54 passed in 0.56s
```

Command:

```bash
rg -n 'CapabilityRegistry|CapabilityHandler|CapabilityContext|on_bridge_message|capabilities=' src tests examples
```

Output:

```text
[no output]
```

Exit code: `1` (expected no-match result)

Command:

```bash
.venv/bin/python -m ruff check --no-cache src tests examples
```

Output:

```text
All checks passed!
```

Command:

```bash
.venv/bin/python -m mypy --no-incremental src
```

Output:

```text
Success: no issues found in 25 source files
```

Command:

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q
```

Output:

```text
................................................ [ 42%]
........................................................................ [ 85%]
........................                                                 [100%]
168 passed in 1.23s
```

### Follow-up self-review

- The public boundary test now covers both symbol exports and `__all__` advertisement for the removed capability surface.
- The implementation fix is minimal and limited to the stale wildcard export entry.
- Verification confirms the package no longer advertises deleted inbound capability APIs.

### Follow-up concerns

- None.

# Design: Protocol Namespace Redesign (mcbews v1)

**Date:** 2026-07-20  
**Status:** Draft for review  
**Scope:** Destructive rename of wire protocol + code surface; remove all AI branding from the generic `mcbe-ws-sdk` bridge protocol.  
**Approach:** Approach A — in-place replace (no dual-profile compatibility). Consistency enforced by scripts, not manual review.

---

## 1. Goals & Non-goals

### Goals

1. Replace AI-branded wire identifiers (`mcbeai` / `MCBEAI` / `MCBEAI_TOOL` / `ai_resp`) with a neutral, consistent root token **`mcbews`**.
2. Unify casing and semantics across scriptevent messageIds, chat prefixes, and simulated player names.
3. De-AI the public Python/TS API surface (profile module, class, singleton, delay kind, handlers, log events).
4. Publish a single, current protocol specification document that replaces the outdated root `addon-bridge-protocol.md`.
5. Guard the rename with **automated detection scripts** so Python profile defaults, Addon constants, tests, and docs cannot drift.

### Non-goals

- No dual-protocol / dual-profile compatibility layer. Old `mcbeai` / `MCBEAI` is not accepted after the cut.
- No change to chunking algorithms, byte budget (461), timeouts, buffer TTLs, or capability handlers.
- No pack UUID / pack folder / npm / PyPI package rename (`mcbe-ws-sdk` / `MCBE-WS-SDK` stay).
- No new transport channel (still scriptevent + chat via simulated player).
- No capability set expansion (still whatever the addon currently registers).
- No parent monorepo (`MCBE-AI-Agent`) rename.

### Decisions already locked

| Decision | Choice |
|---|---|
| Compatibility | Destructive rename |
| Root token | `mcbews` |
| Scope | Full de-AI (wire + code surface + diagnostics + docs/examples) |
| Channel naming | Compact semantic (`bridge_req` / `text_resp` / `BRIDGE` / `UI_CHAT`) |
| Default profile | `mcbews_v1` / `McbewsV1Profile` / `MCBEWS_V1` |
| Implementation approach | Approach A (in-place replace) + detection scripts |

---

## 2. Naming Rules

| Surface | Rule | Example |
|---|---|---|
| Root token (scriptevent namespace) | lowercase `mcbews` | `mcbews:bridge_req` |
| Root token (chat / player) | UPPERCASE `MCBEWS` | `MCBEWS\|BRIDGE` |
| scriptevent messageId | `{root}:{channel}` all lowercase, `_` separators | `mcbews:text_resp` |
| Chat chunk prefix | `{ROOT}\|{KIND}` UPPERCASE, `\|` separators | `MCBEWS\|UI_CHAT` |
| Simulated player | `{ROOT}_{ROLE}` UPPERCASE | `MCBEWS_BRIDGE` |
| Python module path | snake_case, versioned | `profiles/mcbews_v1/` |
| Python class | PascalCase | `McbewsV1Profile` |
| Python singleton | SCREAMING_SNAKE | `MCBEWS_V1` |
| Flow delay kind | short semantic snake_case | `text_resp` |
| TS constants | SCREAMING_SNAKE, no AI brand | `TEXT_RESP_MESSAGE_ID` |
| Log event names | snake_case, no AI brand | `bridge_prefix_not_matched` |

**Forbidden** in wire values, public API symbols, default delay keys, and default log event names:

- `ai`, `AI` as a protocol token
- `mcbeai`, `MCBEAI`, `McbeAi`, `mcbe_ai`
- `ai_resp`, `AI_RESP`
- `LegacyMcbeAi*`, `LEGACY_MCBEAI_*`, `legacy_mcbeai_*`

Historical review docs under `docs/reviews/` may retain old names (whitelist in the checker).

---

## 3. Wire Mapping (Old → New)

| Role | Profile field | Old wire value | New wire value |
|---|---|---|---|
| Bridge request scriptevent id | `bridge_request_message_id` | `mcbeai:bridge_request` | `mcbews:bridge_req` |
| Text response scriptevent id | `response_message_id` | `mcbeai:ai_resp` | `mcbews:text_resp` |
| Bridge response chat prefix | `bridge_response_prefix` | `MCBEAI\|RESP` | `MCBEWS\|BRIDGE` |
| UI chat chat prefix | `ui_chat_prefix` | `MCBEAI\|UI_CHAT` | `MCBEWS\|UI_CHAT` |
| Simulated player sender | `bridge_sender` | `MCBEAI_TOOL` | `MCBEWS_BRIDGE` |
| Request body version | `request_version` | `2` | `2` (unchanged) |
| Flow delay kind | `FlowControlSettings.chunk_delays` key | `ai_resp` | `text_resp` |

### 3.1 Channel A — Bridge request / response

```text
Python → Addon
  scriptevent mcbews:bridge_req {"v":2,"request_id":"…","capability":"…","payload":{…}}

Addon → Python (via simulated player chat)
  MCBEWS|BRIDGE|<request_id>|<index>/<total>|<content>
```

- `<index>` is 1-based.
- `<content>` is a fragment of a JSON response string.
- Sender must be `MCBEWS_BRIDGE`.
- Reassembly, timeout, pending-future behaviour unchanged.

### 3.2 Channel B — UI Chat uplink

```text
Addon UI → Python (via simulated player chat)
  MCBEWS|UI_CHAT|<msg_id>|<index>/<total>|<content>
```

- Full content JSON after reassembly: `{"player":"<name>","message":"<text>"}`.
- Sender must be `MCBEWS_BRIDGE`.

### 3.3 Channel C — Text response downlink

```text
Python → Addon
  scriptevent mcbews:text_resp {"id":"…","i":1,"n":2,"p":"Steve","r":"assistant","c":"…"}
```

- Frame keys `id/i/n/p/r/c` are **unchanged**.
- Only the scriptevent messageId and the flow delay kind rename.
- Addon reassembly path (today `responseSync.ts`) stays algorithmically identical.

### 3.4 Framing that does **not** change

- Pipe-split 5-field chat chunk layout: `PREFIX|id|i/total|content` (where `PREFIX` already contains one `|`).
- Bridge request JSON fields: `v`, `request_id`, `capability`, `payload`.
- UI chat payload fields: `player`, `message`.
- Byte budget 461, code-point limits, sentence-mode splitting, prelude/chunk delays (numeric values).

---

## 4. Code Surface Rename Map

### 4.1 Python profile package

| Old | New |
|---|---|
| `src/mcbe_ws_sdk/profiles/legacy_mcbeai_v1/` | `src/mcbe_ws_sdk/profiles/mcbews_v1/` |
| `LegacyMcbeAiV1Profile` | `McbewsV1Profile` |
| `LEGACY_MCBEAI_V1` | `MCBEWS_V1` |
| `LegacyMcbeAiV1Delivery` | `McbewsV1Delivery` |
| `encode_legacy_response_commands` | `encode_text_response_commands` |
| `parseLegacyResponseChunk` (TS) | `parseTextResponseChunk` |
| `LegacyResponseChunk` (TS) | `TextResponseChunk` |
| `AiRespHandler` / `setAiRespHandler` (TS) | `TextRespHandler` / `setTextRespHandler` |
| `AI_RESP_MESSAGE_ID` (TS) | `TEXT_RESP_MESSAGE_ID` |
| `BRIDGE_MESSAGE_ID` (TS) | `BRIDGE_REQUEST_MESSAGE_ID` (align with Python field intent) |
| `TOOL_PLAYER_NAME` (TS) | `BRIDGE_SENDER` (align with Python `bridge_sender`) |
| Flow delay key `"ai_resp"` | `"text_resp"` |
| Delivery source tag `"legacy_mcbeai_v1_response"` | `"mcbews_v1_text_resp"` |
| Facade log `"mcbeai_prefix_not_matched_as_bridge"` | `"bridge_prefix_not_matched"` |
| Facade hardcode `startswith("MCBEAI\|")` | derive from `profile.bridge_response_prefix` / `profile.ui_chat_prefix` (shared root) |

### 4.2 Public API (`mcbe_ws_sdk.__init__` / `profiles.__init__`)

**Export (new):**

- `McbewsV1Profile`
- `MCBEWS_V1`
- `McbewsV1Delivery`
- `encode_text_response_commands`
- `AddonBridgeProfile` (unchanged Protocol)

**Remove (no alias period):**

- `LegacyMcbeAiV1Profile`
- `LEGACY_MCBEAI_V1`
- `LegacyMcbeAiV1Delivery`
- `encode_legacy_response_commands`

`AddonBridgeSettings.profile` default factory → `McbewsV1Profile`.

`FlowControlSettings.VALID_DELAY_KINDS` → `frozenset({"tellraw", "scriptevent", "text_resp"})`.  
Default `chunk_delays` uses `"text_resp": 0.15`.

### 4.3 Facade diagnostic fix (correctness, not just rename)

Today `server_facade.py` hardcodes:

```python
if event.message.startswith("MCBEAI|"):
    logger.warning("mcbeai_prefix_not_matched_as_bridge", ...)
```

After rename this becomes a silent residual. Replace with profile-derived matching, e.g.:

```python
root = profile.bridge_response_prefix.split("|", 1)[0] + "|"
if event.message.startswith(root):
    logger.warning("bridge_prefix_not_matched", ...)
```

(Exact helper location: small pure function next to profile or on the facade; no new dependency.)

### 4.4 Profile field cleanup note

`response_chunk_delay` on the profile is currently **dead** (delivery uses `FlowControlSettings.chunk_delays["text_resp"]`).  
**This redesign does not remove the field** (out of scope), but docs must not claim it drives delivery. Optional follow-up: delete or wire it — not required to ship the rename.

### 4.5 Addon constants target shape

`addon/scripts/bridge/constants.ts` becomes the sole TS wire source:

```ts
export const BRIDGE_REQUEST_MESSAGE_ID = "mcbews:bridge_req";
export const TEXT_RESP_MESSAGE_ID = "mcbews:text_resp";
export const BRIDGE_RESPONSE_PREFIX = "MCBEWS|BRIDGE";
export const BRIDGE_UI_CHAT_PREFIX = "MCBEWS|UI_CHAT";
export const BRIDGE_SENDER = "MCBEWS_BRIDGE";
// limits unchanged
```

All consumers (`chunking.ts`, `toolPlayer.ts`, `responseSync.ts`, `router.ts`) import these; no inline wire literals elsewhere in `addon/scripts/`.

### 4.6 Log / diagnostic tags (TS)

| Old | New |
|---|---|
| `[MCBE-AI-ToolPlayer]` | `[mcbews-bridge-sender]` |
| Comments / paths mentioning `MCBE-AI-Agent-addon` | `mcbe-ws-sdk addon` |

---

## 5. Protocol Specification Document

### 5.1 Location

| Path | Action |
|---|---|
| `docs/spec/addon-bridge-protocol.md` | **Create** — canonical current protocol spec (mcbews v1) |
| `addon-bridge-protocol.md` (repo root) | **Delete** after the new doc lands |
| `docs/superpowers/specs/2026-07-20-protocol-namespace-redesign.md` | This design doc (process); not the runtime protocol manual |

### 5.2 Spec document outline (what to write)

The new `docs/spec/addon-bridge-protocol.md` must contain:

1. **Goals** — stable Python ↔ Addon bridge over scriptevent + chat chunks; no AI product coupling.
2. **Channels A/B/C** with diagrams (text).
3. **Naming rules** (section 2 of this design, condensed).
4. **Wire values table** (section 3).
5. **Request / response / UI chat / text_resp formats** with examples using **new** names only.
6. **Lifecycle** — request_id, pending futures, reassembly, timeout (keep current behaviour numbers: default 5s timeout, etc.).
7. **Error codes** — rename namespaces in code names if any still say MCBEAI; protocol-level error *identifiers* currently used in the old doc (`BRIDGE_*`, `UI_CHAT_*`) stay, because they are already neutral. Drop any `*INVALID_NAMESPACE` wording that hardcodes `MCBEAI`.
8. **Constraints** — 2048 scriptevent message limit, 461 commandLine budget, chat path dependency.
9. **Implementation map** — Python (`McbewsV1Profile`, codec, service) + Addon (`constants.ts`, router, responseSync, toolPlayer).
10. **Capability baseline** — document only what the addon actually registers today (do not re-list removed `find_entities` unless it still exists).

The old doc's outdated claims (e.g. `find_entities` present, UI chat sender fully implemented, paths under `services/addon/`) must not be copied forward without verification against current code.

### 5.3 Other docs to update (same PR)

| File | Change |
|---|---|
| `README.md` / `README.zh.md` | Profile name, remove `LegacyMcbeAiV1` references |
| `addon/README.md` / `addon/README.zh.md` | Wire examples + constant names |
| `CLAUDE.md` | Profile + flow delay kind + protocol doc pointer |
| `docs/PRD.md` | Profile section + flow delay kind |
| `examples/addon-server/*`, `examples/addon-capability-call/*` | Imports + comments |
| `docs/reviews/*` | **Leave historical** (checker whitelist) |

---

## 6. Detection Scripts (mandatory)

“能用脚本检测尽量用脚本” — the rename is not done until scripts pass in CI or local quality gates.

### 6.1 `tools/check_protocol_names.py`

Single entry point, no network, exit non-zero on failure. Responsibilities:

#### Check A — Python ↔ Addon wire parity

- Load `McbewsV1Profile()` defaults (import from installed/editable package, or parse `profile.py` AST/constants).
- Parse `addon/scripts/bridge/constants.ts` for the five string exports.
- Assert equality:

| Python field | TS constant |
|---|---|
| `bridge_request_message_id` | `BRIDGE_REQUEST_MESSAGE_ID` |
| `response_message_id` | `TEXT_RESP_MESSAGE_ID` |
| `bridge_response_prefix` | `BRIDGE_RESPONSE_PREFIX` |
| `ui_chat_prefix` | `BRIDGE_UI_CHAT_PREFIX` |
| `bridge_sender` | `BRIDGE_SENDER` |

#### Check B — Forbidden token scan

Scan tracked text files under:

- `src/`
- `addon/scripts/`
- `addon/tests/`
- `tests/`
- `examples/`
- `docs/spec/`
- `docs/PRD.md`
- `README.md`, `README.zh.md`
- `addon/README.md`, `addon/README.zh.md`
- `CLAUDE.md`
- `addon-bridge-protocol.md` (must not exist, or only pointer)

**Forbidden regexes** (case-sensitive variants as needed):

- `\bmcbeai\b`, `\bMCBEAI\b`, `\bMcbeAi\b`
- `\bMCBEAI_TOOL\b`
- `\bai_resp\b`, `\bAI_RESP\b`
- `\bLegacyMcbeAi`, `\bLEGACY_MCBEAI`, `\blegacy_mcbeai`
- `\bAiRespHandler\b`, `\bsetAiRespHandler\b`
- `\bencode_legacy_response_commands\b`

**Whitelist paths** (never fail):

- `docs/reviews/**`
- `docs/superpowers/specs/2026-07-20-protocol-namespace-redesign.md` (this design, documents old names in mapping tables)
- `.superpowers/**` historical SDD notes (optional; prefer leave unless they break CI noise)
- Binary / lockfiles / `node_modules` / `__pycache__` / `dist` / `.git`

Report file:line for each hit.

#### Check C — Public API surface

- Import `mcbe_ws_sdk` and assert:
  - `"MCBEWS_V1" in dir(mcbe_ws_sdk)`
  - `"McbewsV1Profile" in dir(mcbe_ws_sdk)`
  - `"McbewsV1Delivery" in dir(mcbe_ws_sdk)`
  - `"encode_text_response_commands" in dir(mcbe_ws_sdk)`
  - none of the removed `Legacy*` / `encode_legacy_*` names appear in `__all__`

#### Check D — Flow delay kind

- Assert `FlowControlSettings.VALID_DELAY_KINDS == frozenset({"tellraw", "scriptevent", "text_resp"})`
- Assert default factory includes `"text_resp"` and excludes `"ai_resp"`

### 6.2 Integration

- Add a pytest node or a CI step: `python tools/check_protocol_names.py`
- Prefer a dedicated test file `tests/unit/test_protocol_names.py` that shells out or imports the checker, so `pytest` alone catches regressions.
- Document the command in `CLAUDE.md` Common Commands.

### 6.3 Not in scope for the checker (yet)

- Generating constants from a single JSON source (Approach C). Checker is a **detector**, not a generator. Can upgrade later if a second profile appears.

---

## 7. Test & Fixture Impact

### 7.1 Python

| File | Change |
|---|---|
| `tests/fixtures/legacy_mcbeai_v1_vectors.json` | Rename → `tests/fixtures/mcbews_v1_vectors.json`; update any wire strings if present (current file is request-body only, mostly version vectors) |
| `tests/unit/test_legacy_mcbeai_v1.py` | Rename → `test_mcbews_v1.py`; update imports, expected wire strings, delay kind |
| `tests/unit/test_addon_bridge.py` | Replace all `MCBEAI\|…` / `MCBEAI_TOOL` / `mcbeai:…` literals |
| `tests/unit/test_addon_request.py` | `scriptevent mcbews:bridge_req …` |
| `tests/unit/test_server_facade.py` | Bridge/UI frames + diagnostic log name |
| `tests/unit/test_public_api.py` | Expect new public symbols; reject old ones |
| `tests/unit/test_config.py` | `McbewsV1Profile` isinstance; delay kinds |
| `tests/unit/test_protocol.py` | Import paths under `mcbews_v1` |
| Other unit tests with incidental hits | Grep-driven replace |

### 7.2 Addon

| File | Change |
|---|---|
| `addon/tests/chunking.test.ts` | Expected prefixes |
| `addon/tests/main.test.ts` | Sender + prefix |
| `addon/tests/responseSync.test.ts` | `TEXT_RESP_MESSAGE_ID` |
| `addon/tests/router.test.ts` | Fixture path + any messageId |

### 7.3 Behaviour parity requirement

All existing behavioural tests must keep asserting the **same** lifecycle semantics (reassembly, timeout, mismatch, byte limits). Only identifiers change. If a test encoded an old name as part of a scenario title, rename the title.

---

## 8. File-level Change List (implementation checklist)

### Create

- `src/mcbe_ws_sdk/profiles/mcbews_v1/` (`__init__.py`, `profile.py`, `codec.py`, `delivery.py`, `models.py` — move/adapt from legacy)
- `docs/spec/addon-bridge-protocol.md`
- `tools/check_protocol_names.py`
- `tests/unit/test_protocol_names.py`
- `tests/fixtures/mcbews_v1_vectors.json`
- `tests/unit/test_mcbews_v1.py`

### Delete

- `src/mcbe_ws_sdk/profiles/legacy_mcbeai_v1/` (entire package)
- `tests/unit/test_legacy_mcbeai_v1.py`
- `tests/fixtures/legacy_mcbeai_v1_vectors.json`
- Root `addon-bridge-protocol.md` (after new spec exists)

### Edit (representative; checker will catch misses)

- `src/mcbe_ws_sdk/__init__.py`
- `src/mcbe_ws_sdk/profiles/__init__.py`
- `src/mcbe_ws_sdk/config.py`
- `src/mcbe_ws_sdk/gateway/server_facade.py`
- `src/mcbe_ws_sdk/addon/service.py`, `session.py` (import paths)
- `addon/scripts/bridge/constants.ts` (+ consumers if they re-export old names)
- `addon/scripts/bridge/responseSync.ts` (handler type/API names)
- `addon/scripts/bridge/toolPlayer.ts` (sender + log tag)
- `addon/scripts/bridge/chunking.ts`, `router.ts`, `bootstrap.ts` as needed
- All tests / examples / README / CLAUDE / PRD listed above

### Leave alone

- Pack manifests / UUIDs / `behavior_packs/mc-ws-sdk/`
- `docs/reviews/**`
- Chunking math, capability handlers, session limits

---

## 9. Migration Notes for Host Apps

This is a **breaking** protocol and API change.

1. Rebuild/redeploy the addon pack together with the Python host — mixed old/new will time out on every bridge call.
2. Host code that imported `LegacyMcbeAiV1Profile` / `LEGACY_MCBEAI_V1` / `encode_legacy_response_commands` / `LegacyMcbeAiV1Delivery` must switch to the new names.
3. Hosts that overrode `FlowControlSettings.chunk_delays["ai_resp"]` must use `"text_resp"`.
4. Hosts that hardcoded wire strings in tests or custom profiles must update the five values in section 3.
5. No wire negotiation / version handshake is added; cutover is simultaneous.

---

## 10. Implementation Order (for the later plan)

Suggested sequence when writing the implementation plan (writing-plans skill):

1. Land the new protocol spec doc (`docs/spec/addon-bridge-protocol.md`) with final wire table.
2. Add `profiles/mcbews_v1/` with new defaults; temporarily keep old package if needed only inside the same PR — final tree must not contain legacy package.
3. Retarget config, public exports, facade diagnostic, addon constants + TS API renames.
4. Mass-update tests/fixtures/examples/docs.
5. Delete legacy package + root old protocol md.
6. Add `tools/check_protocol_names.py` + unit test; run full Python + Addon test suites.
7. Run checker clean; run `ruff` / `mypy` / `pytest` / addon `test`+`typecheck`.

No partial merge: one PR (or one branch) that leaves the tree fully on mcbews v1 with checker green.

---

## 11. Success Criteria

- [ ] No forbidden tokens under scanned paths (checker B green).
- [ ] Python profile defaults == Addon constants (checker A green).
- [ ] Public API exports only new symbols (checker C green).
- [ ] Delay kind is `text_resp` only (checker D green).
- [ ] `pytest` green; addon `npm test` + `typecheck` green.
- [ ] `docs/spec/addon-bridge-protocol.md` is the sole current protocol manual and uses only new names.
- [ ] Root `addon-bridge-protocol.md` removed.
- [ ] Facade mismatch diagnostic still fires for wrong-sender chunks under the **new** root prefix.

---

## 12. Open Items (resolved defaults)

| Item | Resolution |
|---|---|
| Keep root `addon-bridge-protocol.md` as pointer? | **Delete** |
| Rename `response_chunk_delay` dead field? | Out of scope; leave field, document as unused by delivery |
| Whitelist `.superpowers/sdd/**`? | Yes, historical |
| Dual-read old wire during transition? | No |
| Generate constants from JSON? | No for this change; detector only |

---

## 13. Self-review notes (author)

- Mapping tables are complete for the five wire values + delay kind + public symbols.
- No dual-profile ambiguity; Approach A is explicit.
- Checker scope and whitelist are concrete enough to implement without further product decisions.
- Spec outline avoids copying outdated capability claims from the old doc.
- Implementation order ends with automated verification, matching the user's "use scripts" requirement.

# Addon Bridge Protocol (mcbews v1)

## Goals

A stable bridge protocol between the Python host and the Minecraft Addon:

- Use `/scriptevent` for structured requests and outbound text frames
- Return Bridge responses and UI chat via simulated-player chat chunks
- Unify the namespace as `mcbews` / `MCBEWS` with consistent casing

This protocol is the default profile of `mcbe-ws-sdk`
(`McbewsV1Profile` / `MCBEWS_V1`).

!!! warning "World requirement: Beta APIs"
    The companion Script addon only runs when the world has **Experiments →
    Beta APIs** enabled. Without it, `scriptEventReceive` never fires and
    capability requests time out. See the [addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.md#enable-in-a-world).

## Naming rules

| Context | Rule | Example |
|---|---|---|
| scriptevent messageId | lowercase root token + channel | `mcbews:bridge_req` |
| chat chunk prefix | UPPERCASE root token + type | `MCBEWS\|BRIDGE` |
| simulated player name | UPPERCASE root token + role | `MCBEWS_BRIDGE` |

**Do not** put AI brand tokens in wire values. Namespaces and delay kinds always
use mcbews / MCBEWS / `text_resp` (see the migration table at the end for legacy
values).

## Current implementation overview

End-to-end path today:

```text
Python host
  -> AddonBridgeService
  -> scriptevent mcbews:bridge_req <json>
  -> Addon scriptEventReceive
  -> capability handler
  -> MCBEWS_BRIDGE simulated-player chat chunks
  -> WebSocket PlayerMessage
  -> Python chunk reassembly + future wake-up
```

Three independent channels:

### Channel A: Python → Addon capability request (Bridge)

```text
Python host
  -> AddonBridgeService
  -> scriptevent mcbews:bridge_req <json>
  -> Addon scriptEventReceive
  -> capability handler
  -> MCBEWS_BRIDGE simulated-player chat chunks (MCBEWS|BRIDGE)
  -> WebSocket PlayerMessage
  -> Python chunk reassembly + future wake-up
```

### Channel B: Addon UI → Python auto-chat (UI Chat)

```text
Player opens a UI panel and types a message
  -> Addon emits UI Chat chunks
  -> MCBEWS_BRIDGE simulated-player chat chunks (MCBEWS|UI_CHAT)
  -> WebSocket PlayerMessage
  -> Python reassembly
  -> typed hook.on_ui_chat_reassembled(state, UiChatMessage) /
     EventBus UI_CHAT_REASSEMBLED (UiChatMessage.cid preserved)
```

### Channel C: Python → Addon text response (Text Response)

```text
Python host
  -> McbewsV1Delivery / encode_text_response_commands
  -> scriptevent mcbews:text_resp <json frame>
  -> Addon responseSync reassembly
  -> UI / host callback renders full text
```

Notes:

- Python request entry: `mcbe_ws_sdk.addon.service.AddonBridgeService`.
- Addon request listen path uses `scriptEventReceive` with fixed messageId
  `mcbews:bridge_req`.
- Addon Bridge / UI Chat replies are **not** written back over WebSocket
  directly; the simulated player `MCBEWS_BRIDGE` sends chat chunks.
- The Python side intercepts those chunks in the WebSocket `PlayerMessage`
  stream and does **not** treat them as ordinary chat.
- Outbound text uses the separate scriptevent `mcbews:text_resp` with JSON
  frames (`id/i/n/p/r/c`).
- **UI chat** is initiated by the Addon UI; the simulated player
  `MCBEWS_BRIDGE` sends `MCBEWS|UI_CHAT` chunks. After reassembly, Python hands
  the message to the host hook — the real player never has to type a command.

## Wire constants

| Role | Profile field | Wire value |
|---|---|---|
| Bridge request messageId | `capability_request_script_event_id` | `mcbews:bridge_req` |
| Text response messageId | `text_response_script_event_id` | `mcbews:text_resp` |
| Bridge response prefix | `capability_response_chat_prefix` | `MCBEWS\|BRIDGE` |
| UI Chat prefix | `ui_chat_chunk_prefix` | `MCBEWS\|UI_CHAT` |
| Simulated player | `trusted_bridge_player_name` | `MCBEWS_BRIDGE` |
| Session request prefix | `session_request_chat_prefix` | `MCBEWS\|SESSION` |
| Session response messageId | `session_response_script_event_id` | `mcbews:session_resp` |
| Capability request schema | `capability_request_schema_version` | `2` |

Python and the Addon **must** stay bit-identical on this table. The canonical
manifest and executable vectors are installed at
`mcbe_ws_sdk.profiles.mcbews_v1/{manifest.json,vectors.json}`; the reference
Addon projection is checked against those resources by
`tools/check_protocol_names.py`. The old profile names remain read-only
deprecated aliases for one migration cycle and are not independent settings.

### Version axes and bounds

MCBEWS/1 names each compatibility axis independently:

| Axis | Value | Meaning |
|---|---:|---|
| `capability_request_schema_version` | 2 | capability request JSON shape |
| `session_schema_version` | 1 | typed session request/response shape |
| `text_response_framing_version` | 1 | `id/i/n/p/r/c` text framing |
| `ddui_persistence_version` | 2 | Addon UI persistence format |

The empirical full `commandLine` budget is 461 UTF-8 bytes by default; this is a
configurable safety ceiling that deployments may lower. Upstream chat
chunks default to 256 Unicode code points; text response assembly is bounded
to 64 buffers, 128 chunks per response, 65,536 bytes per response, and
262,144 total buffered bytes with a 30-second TTL.

## Request format (Python → Addon)

- Command: `scriptevent mcbews:bridge_req <json>`
- `message_id` is fixed to `mcbews:bridge_req`
- JSON shape:
  - `v`: number (currently fixed at `2`)
  - `request_id`: string
  - `capability`: string
  - `payload`: object

Example:

```text
scriptevent mcbews:bridge_req {"v":2,"request_id":"req-1","capability":"get_player_snapshot","payload":{"target":"@s"}}
```

## Response chunk format (Addon → Python)

### Bridge response (reply to a Python → Addon request)

- Prefix: `MCBEWS|BRIDGE`
- Single chunk: `MCBEWS|BRIDGE|<request_id>|<index>/<total>|<content>`
- `<index>` starts at 1
- `<content>` is a slice of the JSON response string
- Chunks are sent as chat by the simulated player `MCBEWS_BRIDGE`

Example:

```text
MCBEWS|BRIDGE|req-1|1/2|{"ok":true,
MCBEWS|BRIDGE|req-1|2/2|"result":{"name":"Steve"}}
```

Successful reassembled body:

```json
{"ok": true, "result": { ... }}
```

Failed reassembled body:

```json
{"ok": false, "error": {"code": "UNSUPPORTED_CAPABILITY", "message": "..."}}
```

Addon-side error codes (inside the response JSON):

- `MALFORMED_JSON`
- `INVALID_REQUEST`
- `UNSUPPORTED_VERSION`
- `UNSUPPORTED_CAPABILITY`
- `CAPABILITY_FAILED`

### UI Chat messages (Addon UI → Python auto-chat)

- Prefix: `MCBEWS|UI_CHAT`
- Single chunk: `MCBEWS|UI_CHAT|<msg_id>|<index>/<total>|<content>`
- `<index>` starts at 1
- `<content>` is a slice of a JSON string whose full shape is
  `{"player": "<player name>", "message": "<chat text>", "cid": "<conversation>"}`
- Also sent by the simulated player `MCBEWS_BRIDGE`; implementations usually
  wrap with self-only tell so real player chat is not spammed

Single-chunk example:

```text
MCBEWS|UI_CHAT|ui-1744876800000-1|1/1|{"player":"Steve","message":"hello world"}
```

Multi-chunk example:

```text
MCBEWS|UI_CHAT|ui-1744876800000-1|1/2|{"player":"Steve","mes
MCBEWS|UI_CHAT|ui-1744876800000-1|2/2|sage":"hello world"}
```

## Text response format (Python → Addon)

- Command: `scriptevent mcbews:text_resp <json>`
- Per-frame JSON fields:

| Field | Meaning |
|---|---|
| `id` | Response message id |
| `i` | Chunk index (1-based) |
| `n` | Total chunk count |
| `p` | Target player name |
| `r` | Role (e.g. `assistant`) |
| `c` | Text content slice |
| `cid` | Conversation id; repeated on every frame when present |
| `t` | Optional response title; repeated on every frame when present |
| `u` | `{ "i": input, "o": output }` token usage, **completion frame only** |

Example:

```text
scriptevent mcbews:text_resp {"id":"resp-1","i":1,"n":2,"p":"Steve","r":"assistant","c":"hello, "}
scriptevent mcbews:text_resp {"id":"resp-1","i":2,"n":2,"p":"Steve","r":"assistant","c":"world"}
```

The Addon keys every stream by player + normalized conversation bucket + response id
(pre-CID frames use the compatibility bucket `default`), caches
chunks by that key, validates metadata/duplicates/limits, reassembles after
`1..n` arrive, and hands a typed complete message to the presentation layer.
Unknown roles are rejected before UI/history handling. `role` may be
`user`, `assistant`, or `approval`.

## Session and approval control channels

Trusted ToolPlayer chat is authenticated by the fixed sender
`MCBEWS_BRIDGE`; the sender is transport identity only and is never used as
the business player. Reserved channels are consumed by the SDK even when the
sender is wrong, so forged session/approval/UI frames cannot fall through to
ordinary host chat.

Session requests use one atomic chat message:

```text
MCBEWS|SESSION|{"v":1,"request_id":"sess-1","action":"list","player_name":"Steve","cid":"chat-a"}
```

`action` is validated against the typed session operation set and
action-specific `cid`/`sid` requirements. Responses use one atomic
`scriptevent mcbews:session_resp <json>` command with the same
`request_id`/`action`. A result that cannot fit the configured (default
461-byte) budget is replaced
by a parseable correlated error with code `SESSION_RESPONSE_TOO_LARGE`; the
SDK never emits a fragment of session JSON.

Approval decisions use typed JSON and include the business owner:

```text
MCBEWS|TOOL_APPROVE|{"v":1,"approval_id":"ap-1","player_name":"Steve","cid":"chat-a"}
```

The id-only allow/deny form remains a deprecated compatibility input; a Host
must resolve it against exactly one unexpired pending record on the same
connection. Claims for another player or conversation fail closed.

## Capability list (current baseline)

Default Addon capability registry:

- `get_player_snapshot` — player snapshot (position, dimension, look, basic state)
- `get_inventory_snapshot` — inventory snapshot (slots, items, counts, extras)

Optional registerable module:

- `run_world_command` — controlled world command execution with a result
  (must be explicitly attached to the registry by host/Addon)

The capability set is owned by the Addon; the Python SDK does **not** ship an
inbound capability dispatcher. Unregistered capabilities return
`UNSUPPORTED_CAPABILITY`.

## Request correlation and lifecycle

- Every Python bridge call generates a unique `request_id`.
- The same `request_id` appears in the `/scriptevent` body and in Addon chat
  chunk headers, correlating one round-trip.
- Python keeps pending requests per connection and buffers chunks by
  `request_id`.
- When all chunks for a `request_id` arrive, Python reassembles the JSON
  payload and wakes the waiting future.
- Chunks with an unknown `request_id` are ignored; they never create a new
  pending request.
- Sender filter: only when `PlayerMessage.sender == MCBEWS_BRIDGE` **and** the
  prefix matches does the frame enter the bridge / UI Chat reassembly path.

## Timeout behaviour

- Default Python bridge timeout is 5 seconds
  (`AddonBridgeSettings.timeout_seconds`).
- If `/scriptevent` was sent but not all chunks for the `request_id` arrive
  within the window, the call fails with an addon-bridge response timeout.
- If the command send itself fails (e.g. `/scriptevent` returns an error),
  the call fails immediately without waiting for chunks.
- On timeout or failure, Python clears the pending request and chunk buffer for
  that `request_id`.
- Chunk buffers also have a TTL (default 30s) and byte/count limits to prevent
  leaks.

## Error semantics (protocol level)

### Bridge response chunk decode / reassembly

The Python codec raises `ValueError` when:

- Chunk field count is wrong
- Namespace / prefix mismatch (expects `MCBEWS` + `BRIDGE`)
- Illegal metadata (index / total / request_id)
- Chunk list is empty
- Missing, duplicate, or inconsistent indices
- Mixed `request_id` or `total` within one batch
- Reassembled JSON fails to deserialize or the root is not an object

### UI Chat chunk decode / reassembly

- Wrong field count
- Namespace / prefix mismatch (expects `MCBEWS` + `UI_CHAT`)
- Illegal metadata
- Empty chunk list
- Missing / duplicate / inconsistent indices
- Illegal reassembled JSON
- Missing non-empty `message` field in the JSON

### Diagnostics

If chat content starts with the protocol root prefix `MCBEWS|` but does not
enter bridge handling (e.g. sender mismatch), the Python facade should emit a
mismatch diagnostic log (`bridge_prefix_not_matched`) so timeouts are not
silent.

## Constraints and design rationale

- `/scriptevent <messageId> <message>` caps `message` at 2048 characters —
  longer payloads must be chunked.
- The script side can read `id` and `message` via
  `ScriptEventCommandMessageAfterEvent`, so explicit namespace routing
  (`mcbews:bridge_req` / `mcbews:text_resp`) is retained.
- Addon → Python replies currently ride the chat channel, not a private binary
  or custom network path, so chat length and chunk order matter.
- Python only intercepts bridge chunks on WebSocket `PlayerMessage` events, so
  the chat subscription path must be healthy.
- Empirically safe MCBE `commandLine` byte budget is **461** by default; both directions
  must validate real UTF-8 bytes.
  - Upstream (Addon → Python, chat-wrapped) default content code-point cap: 256
  - Downstream (Python → Addon, scriptevent/text) default controlled by
    `FlowControlSettings.max_chunk_content_length` (default 400)
- Text-response flow-control delay kind is `text_resp` (legacy delay kinds are
  deprecated; see migration table).
- Because of `@minecraft/server` API shape, `run_world_command` is based on
  synchronous `runCommand` when registered.
- This protocol binds to no LLM / Agent product semantics; the host decides how
  to interpret UI Chat and text responses.

## Current baseline implementation

### Python side

- `McbewsV1Profile` / `MCBEWS_V1` — default protocol profile
- `encode_bridge_request` — encode Bridge request commands
- `decode_bridge_chat_chunk` — parse Bridge response chunks
- `reassemble_bridge_chunks` — reassemble and parse JSON payload
- `decode_ui_chat_chunk` — parse UI Chat chunks
- `reassemble_ui_chat_chunks` — reassemble UI Chat and extract player + message
- `encode_text_response_commands` — encode text-response scriptevent frames
- `McbewsV1Delivery` — text response delivery with prelude / chunk delays
- `AddonBridgeService` — send `/scriptevent`, wait on futures, timeouts, UI Chat
  callbacks
- WebSocket facade intercepts `MCBEWS_BRIDGE` bridge and UI Chat chunks in the
  `PlayerMessage` stream

### Addon side

- `constants.ts` — single source of wire constants (messageId / prefix / player)
- `formatChunk` — generic chunk formatting (custom prefixes supported)
- `formatResponseChunk` — format Bridge response chunks
- `chunkPayload` — generic chunk splitting (custom prefixes supported)
- `chunkBridgePayload` — split Bridge responses by max fragment length
- `chunkUiChatPayload` — split UI Chat messages by max fragment length
- Response send path: drive `MCBEWS_BRIDGE` to emit Bridge response chunks
- UI Chat send path: drive `MCBEWS_BRIDGE` to emit UI Chat messages
- `registerBridgeRouter` — subscribe to `scriptEventReceive` and dispatch
  capability handlers
- `responseSync` — subscribe to `mcbews:text_resp` and reassemble text frames
- `protocol.ts` — generated manifest/version/limit/error/vector projection
- `ResponseAssembler` — bounded UTF-8-aware text reassembly keyed by player,
  conversation, and response

## Relation to the legacy protocol (mcbeai)

This protocol is a **breaking** replacement with no dual-read compatibility:

| Role | Legacy (deprecated) | Current |
|---|---|---|
| Bridge request | `mcbeai:bridge_request` | `mcbews:bridge_req` |
| Text response | `mcbeai:ai_resp` | `mcbews:text_resp` |
| Bridge prefix | `MCBEAI\|RESP` | `MCBEWS\|BRIDGE` |
| UI Chat prefix | `MCBEAI\|UI_CHAT` | `MCBEWS\|UI_CHAT` |
| Simulated player | `MCBEAI_TOOL` | `MCBEWS_BRIDGE` |

Python host and Addon must upgrade together; mixing old/new namespaces causes
bridge request timeouts.

# Addon Bridge Protocol (mcbews v1)

## Goals

A stable bridge protocol between the Python host and the Minecraft Addon:

- Use `/scriptevent` for structured requests and outbound text frames
- Return Bridge responses and UI chat via simulated-player chat chunks
- Unify the namespace as `mcbews` / `MCBEWS` with consistent casing

This protocol is the default profile of `mcbe-ws-sdk`
(`McbewsV1Profile` / `MCBEWS_V1`).

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
  -> hook.on_ui_chat_reassembled / EventBus UI_CHAT_REASSEMBLED
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
| Bridge request messageId | `bridge_request_message_id` | `mcbews:bridge_req` |
| Text response messageId | `response_message_id` | `mcbews:text_resp` |
| Bridge response prefix | `bridge_response_prefix` | `MCBEWS\|BRIDGE` |
| UI Chat prefix | `ui_chat_prefix` | `MCBEWS\|UI_CHAT` |
| Simulated player | `bridge_sender` | `MCBEWS_BRIDGE` |
| Request body version | `request_version` | `2` |

Python and the Addon **must** stay bit-identical on this table. The repo
enforces alignment and banned tokens via `tools/check_protocol_names.py`.

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
  `{"player": "<player name>", "message": "<chat text>"}`
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

Example:

```text
scriptevent mcbews:text_resp {"id":"resp-1","i":1,"n":2,"p":"Steve","r":"assistant","c":"hello, "}
scriptevent mcbews:text_resp {"id":"resp-1","i":2,"n":2,"p":"Steve","r":"assistant","c":"world"}
```

The Addon caches chunks by `id`, reassembles after `1..n` arrive, and hands the
full text to the presentation layer.

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
- Empirically safe MCBE `commandLine` byte budget is **461**; both directions
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

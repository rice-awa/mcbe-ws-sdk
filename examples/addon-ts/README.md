# mcbe-ws-sdk TypeScript Addon Example

A minimal, self-contained reference that demonstrates the **bridge wire
protocol** used by the mcbe-ws-sdk Python library.  It shows how an addon
receives streamed AI response chunks via `mcbeai:ai_resp` scriptevents,
buffers them, and reassembles the full message — without any UI framework,
persistence, or third-party dependencies beyond `@minecraft/server`.

## Structure

| File            | Purpose |
|-----------------|---------|
| `constants.ts`  | Protocol constants matching `AddonProtocolConfig` |
| `chunk.ts`      | Pure encode/decode utilities for `MCBEAI|RESP` chunks |
| `handler.ts`    | Scriptevent subscriber + per-message chunk buffer |
| `bootstrap.ts`  | Early/late init lifecycle entry-points |

## Usage

1. Copy the four `.ts` files into your behaviour pack's `scripts/` folder.
2. Call `initializeEarly()` from your pack's main entry (before world
   access — it only registers event subscriptions).
3. Call `initializeAfterWorldLoad()` after the world is ready.
4. This is a **reference**; it is not a standalone addon.  You must wire
   tool-player creation and UI forms yourself (see the full addon repo).

## Matching Python SDK

The Python-side constants in `AddonProtocolConfig` default to the same
values as `constants.ts`.  The `mcbe_ws_sdk.addon.protocol` module provides
the Python counterparts of `chunk.ts`:

- `encode_bridge_request()` / `decode_bridge_chat_chunk()` — request framing
- `encode_ai_response_chunks()` / `decode_ui_chat_chunk()` — stream chunks

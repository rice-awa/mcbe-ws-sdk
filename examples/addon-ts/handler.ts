/**
 * Minimal ``mcbeai:ai_resp`` scriptevent subscriber with
 * chunk-buffer reassembly.
 *
 * Does NOT depend on ``@minecraft/server-ui``, ``DynamicProperty``,
 * history, or any UI state — pure protocol-layer receiver.
 */

import { system } from "@minecraft/server";

import { AI_RESP_MESSAGE_ID } from "./constants";
import { decodeBridgeChunk, reassembleBridgeChunks, type BridgeChunk } from "./chunk";

// ── types ─────────────────────────────────────────────────────

/**
 * A single ``mcbeai:ai_resp`` scriptevent JSON payload.
 *
 * Fields: ``id`` (message id), ``i`` (1-based chunk index),
 * ``n`` (total chunks), ``p`` (player name), ``r`` (role),
 * ``c`` (content fragment).
 */
export interface AiRespChunk {
  id: string;
  i: number;
  n: number;
  p: string;
  r: string;
  c: string;
}

/**
 * Callback invoked when all chunks for a message have been received
 * and reassembled.
 *
 * @param playerName  The target player (from the ``p`` field).
 * @param role        The message role, e.g. ``"assistant"`` or ``"tool"``.
 * @param fullText    The fully reassembled text content.
 */
export type MessageCallback = (
  playerName: string,
  role: string,
  fullText: string,
) => void;

// ── state ─────────────────────────────────────────────────────

/** Per-message chunk buffer: messageId -> Map<chunkIndex, BridgeChunk> */
const buffers = new Map<string, Map<number, BridgeChunk>>();

/** Whether the scriptevent subscription has been registered. */
let isRegistered = false;

/** The host-registered message callback. */
let onMessage: MessageCallback | null = null;

// ── public API ────────────────────────────────────────────────

/**
 * Register (or replace) the callback fired when a complete
 * AI response message has been reassembled.
 */
export function setMessageCallback(fn: MessageCallback): void {
  onMessage = fn;
}

/**
 * Subscribe to ``system.afterEvents.scriptEventReceive`` for
 * ``mcbeai:ai_resp`` events.  Idempotent — safe to call multiple
 * times.
 */
export function registerAiRespSyncHandler(): void {
  if (isRegistered) {
    return;
  }
  isRegistered = true;

  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id !== AI_RESP_MESSAGE_ID) {
      return;
    }

    let raw: AiRespChunk;
    try {
      raw = JSON.parse(event.message) as AiRespChunk;
    } catch {
      // Silently ignore unparseable payloads.
      return;
    }

    const { id, i, n, p: playerName, r: role, c: content } = raw;

    // Basic structural validation.
    if (!id || i <= 0 || n <= 0 || i > n) {
      return;
    }

    // Build a BridgeChunk from the JSON fields (the chunk content
    // in this path is just the raw text fragment — no pipe encoding).
    const chunk: BridgeChunk = {
      requestId: id,
      chunkIndex: i,
      totalChunks: n,
      content,
    };

    // Buffer.
    let entry = buffers.get(id);
    if (!entry) {
      entry = new Map();
      buffers.set(id, entry);
    }
    entry.set(i, chunk);

    // Not yet complete.
    if (entry.size < n) {
      return;
    }

    // Reassemble.
    let fullText: string;
    try {
      fullText = reassembleBridgeChunks([...entry.values()]);
    } catch {
      // If reassembly fails, discard the buffer and wait for a retry
      // (the Python side may re-send).
      buffers.delete(id);
      return;
    }

    // Clean up.
    buffers.delete(id);

    // Deliver.
    if (onMessage) {
      onMessage(playerName, role, fullText);
    }
  });
}

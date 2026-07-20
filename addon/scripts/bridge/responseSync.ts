import { system } from "@minecraft/server";

import { TEXT_RESP_MESSAGE_ID } from "./constants";
import { utf8ByteLength } from "./chunking";

// ── Limits ──

export type ResponseSyncLimits = {
  ttlMs: number;
  maxBuffers: number;
  maxChunksPerMessage: number;
  maxMessageBytes: number;
};

export const DEFAULT_RESPONSE_SYNC_LIMITS: ResponseSyncLimits = {
  ttlMs: 30_000,
  maxBuffers: 64,
  maxChunksPerMessage: 128,
  maxMessageBytes: 64 * 1024,
};

// ── Buffer state (internal) ──

type BufferState = {
  createdAt: number;
  total: number;
  playerName: string;
  role: string;
  byteLength: number;
  chunks: Map<number, string>;
};

// ── Public types ──

export type TextResponseChunk = {
  id: string;
  i: number;
  n: number;
  p: string;
  r: string;
  c: string;
};

export type ReassembledResponse = {
  playerName: string;
  role: string;
  text: string;
};

/** 重组完成后的回调签名：(playerName, role, fullText) */
export type TextRespHandler = (playerName: string, role: string, text: string) => void;

// ── Chunk parser ──

export function parseTextResponseChunk(value: unknown): TextResponseChunk | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if (
    typeof item.id !== "string" ||
    typeof item.i !== "number" ||
    typeof item.n !== "number" ||
    typeof item.p !== "string" ||
    typeof item.r !== "string" ||
    typeof item.c !== "string"
  )
    return null;
  return { id: item.id, i: item.i, n: item.n, p: item.p, r: item.r, c: item.c };
}

// ── Bounded ResponseAssembler ──

export class ResponseAssembler {
  private readonly buffers = new Map<string, BufferState>();

  constructor(
    private readonly limits: ResponseSyncLimits = DEFAULT_RESPONSE_SYNC_LIMITS,
    private readonly now: () => number = Date.now
  ) {}

  get bufferCount(): number {
    return this.buffers.size;
  }

  clear(): void {
    this.buffers.clear();
  }

  pruneExpired(): void {
    const cutoff = this.now();
    for (const [id, state] of this.buffers) {
      if (cutoff - state.createdAt >= this.limits.ttlMs) {
        this.buffers.delete(id);
      }
    }
  }

  push(chunk: TextResponseChunk): ReassembledResponse | null {
    this.pruneExpired();

    // Validate chunk metadata
    if (
      !chunk.id ||
      !chunk.p ||
      !chunk.r ||
      !Number.isInteger(chunk.i) ||
      !Number.isInteger(chunk.n) ||
      chunk.i < 1 ||
      chunk.i > chunk.n ||
      chunk.n > this.limits.maxChunksPerMessage
    ) {
      return null;
    }

    let state = this.buffers.get(chunk.id);

    if (!state) {
      // New buffer entry — check maxBuffers limit
      if (this.buffers.size >= this.limits.maxBuffers) return null;

      state = {
        createdAt: this.now(),
        total: chunk.n,
        playerName: chunk.p,
        role: chunk.r,
        byteLength: 0,
        chunks: new Map<number, string>(),
      };
      this.buffers.set(chunk.id, state);
    } else if (state.total !== chunk.n || state.playerName !== chunk.p || state.role !== chunk.r) {
      // Metadata conflict — delete buffer and return null
      this.buffers.delete(chunk.id);
      return null;
    }

    // Check for duplicate index
    const existing = state.chunks.get(chunk.i);
    if (existing !== undefined) {
      if (existing !== chunk.c) {
        // Conflicting content at same index — delete buffer
        this.buffers.delete(chunk.id);
      }
      // Same content = idempotent; either way, no new complete response
      return null;
    }

    // Check byte limit
    const nextBytes = state.byteLength + utf8ByteLength(chunk.c);
    if (nextBytes > this.limits.maxMessageBytes) {
      this.buffers.delete(chunk.id);
      return null;
    }

    // Store chunk
    state.chunks.set(chunk.i, chunk.c);
    state.byteLength = nextBytes;

    // Check if all chunks received
    if (state.chunks.size !== state.total) return null;

    // Verify all indices 1..n are present
    const ordered: string[] = [];
    for (let index = 1; index <= state.total; index += 1) {
      const content = state.chunks.get(index);
      if (content === undefined) return null;
      ordered.push(content);
    }

    // Complete — delete buffer and return result
    this.buffers.delete(chunk.id);
    return {
      playerName: state.playerName,
      role: state.role,
      text: ordered.join(""),
    };
  }
}

// ── Module state ──

let handler: TextRespHandler | null = null;
let isRegistered = false;
const assembler = new ResponseAssembler();

/** 注册一个回调，在 mcbews:text_resp 报文重组完成后调用。 */
export function setTextRespHandler(fn: TextRespHandler): void {
  handler = fn;
}

/** 注册 scriptEventReceive 订阅，监听 mcbews:text_resp 分片。 */
export function registerResponseSyncHandler(): void {
  if (isRegistered) return;
  isRegistered = true;

  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id !== TEXT_RESP_MESSAGE_ID) return;

    // Same as bridge router: /wsserver-delivered scriptevents may arrive as
    // Entity rather than Server. Dropping them silently breaks text response sync.
    if (event.sourceType !== "Server") {
      console.warn(`[respSync] accepting non-Server scriptevent: id=${event.id}, sourceType=${event.sourceType}`);
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(event.message);
    } catch {
      return;
    }

    const chunk = parseTextResponseChunk(parsed);
    if (!chunk) return;

    const result = assembler.push(chunk);
    if (result && handler) {
      handler(result.playerName, result.role, result.text);
    }
  });
}

// ── Internal testing helpers ──

/** @internal */
export function _testingReset(): void {
  handler = null;
  isRegistered = false;
  assembler.clear();
}

import { system } from "@minecraft/server";

import {
  RESPONSE_BUFFER_TTL_MS,
  RESPONSE_MAX_BUFFERS,
  RESPONSE_MAX_CHUNKS_PER_MESSAGE,
  RESPONSE_MAX_MESSAGE_BYTES,
  RESPONSE_MAX_TOTAL_BUFFER_BYTES,
  TEXT_RESPONSE_SCRIPT_EVENT_ID,
  TEXT_RESPONSE_ALLOWED_ROLES,
} from "./protocol";
import { utf8ByteLength } from "./chunking";

// ── Limits ──

export type ResponseSyncLimits = {
  ttlMs: number;
  maxBuffers: number;
  maxChunksPerMessage: number;
  maxMessageBytes: number;
  maxTotalBufferBytes?: number;
};

export const DEFAULT_RESPONSE_SYNC_LIMITS: ResponseSyncLimits = {
  ttlMs: RESPONSE_BUFFER_TTL_MS,
  maxBuffers: RESPONSE_MAX_BUFFERS,
  maxChunksPerMessage: RESPONSE_MAX_CHUNKS_PER_MESSAGE,
  maxMessageBytes: RESPONSE_MAX_MESSAGE_BYTES,
  maxTotalBufferBytes: RESPONSE_MAX_TOTAL_BUFFER_BYTES,
};

// ── Buffer state (internal) ──

type Usage = { i: number; o: number };

/** Compatibility bucket for pre-CID text response frames. */
export const DEFAULT_CONVERSATION_ID = "default" as const;

type StoredChunk = {
  content: string;
  usage?: Usage;
};

type BufferState = {
  lastUpdatedAt: number;
  total: number;
  playerName: string;
  role: TextResponseRole;
  conversationId: string;
  title?: string;
  usage?: Usage;
  byteLength: number;
  chunks: Map<number, StoredChunk>;
};

// ── Public types ──

export type TextResponseRole = (typeof TEXT_RESPONSE_ALLOWED_ROLES)[number];

export type TextResponseChunk = {
  id: string;
  i: number;
  n: number;
  p: string;
  r: string;
  c: string;
  cid?: string;
  t?: string;
  u?: Usage;
};

export type ReassembledResponse = {
  playerName: string;
  role: TextResponseRole;
  text: string;
  responseId: string;
  conversationId: string;
  title?: string;
  usage?: Usage;
};

/** Legacy callback signature retained for one migration cycle. */
export type TextRespHandler = (playerName: string, role: string, text: string) => void;
export type TextResponseMessageHandler = (message: ReassembledResponse) => void;

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
    typeof item.c !== "string" ||
    !Number.isInteger(item.i) ||
    !Number.isInteger(item.n) ||
    item.i < 1 ||
    item.i > item.n ||
    item.n > RESPONSE_MAX_CHUNKS_PER_MESSAGE ||
    !item.id ||
    !item.p ||
    !TEXT_RESPONSE_ALLOWED_ROLES.includes(item.r as TextResponseRole)
  ) {
    return null;
  }
  if (item.cid !== undefined && (typeof item.cid !== "string" || !item.cid)) return null;
  if (item.t !== undefined && typeof item.t !== "string") return null;
  let usage: Usage | undefined;
  if (item.u !== undefined) {
    if (typeof item.u !== "object" || item.u === null || Array.isArray(item.u)) {
      return null;
    }
    const usageObject = item.u as Record<string, unknown>;
    if (
      typeof usageObject.i !== "number" ||
      typeof usageObject.o !== "number" ||
      !Number.isInteger(usageObject.i) ||
      !Number.isInteger(usageObject.o) ||
      usageObject.i < 0 ||
      usageObject.o < 0 ||
      item.i !== item.n
    ) {
      return null;
    }
    usage = {
      i: usageObject.i,
      o: usageObject.o,
    };
  }
  return {
    id: item.id,
    i: item.i,
    n: item.n,
    p: item.p,
    r: item.r,
    c: item.c,
    ...(item.cid === undefined ? {} : { cid: item.cid as string }),
    ...(item.t === undefined ? {} : { t: item.t as string }),
    ...(usage === undefined ? {} : { u: usage }),
  };
}

// ── Bounded ResponseAssembler ──

export class ResponseAssembler {
  private readonly buffers = new Map<string, BufferState>();
  private totalBufferedBytes = 0;

  constructor(
    private readonly limits: ResponseSyncLimits = DEFAULT_RESPONSE_SYNC_LIMITS,
    private readonly now: () => number = Date.now
  ) {}

  get bufferCount(): number {
    return this.buffers.size;
  }

  get bufferedBytes(): number {
    return this.totalBufferedBytes;
  }

  clear(): void {
    this.buffers.clear();
    this.totalBufferedBytes = 0;
  }

  clearForPlayer(playerName: string): void {
    for (const [key, state] of this.buffers) {
      if (state.playerName === playerName) this.drop(key);
    }
  }

  pruneExpired(): void {
    const cutoff = this.now();
    for (const [key, state] of this.buffers) {
      if (cutoff - state.lastUpdatedAt >= this.limits.ttlMs) this.drop(key);
    }
  }

  push(chunk: TextResponseChunk): ReassembledResponse | null {
    this.pruneExpired();
    if (
      !chunk.id ||
      !chunk.p ||
      !TEXT_RESPONSE_ALLOWED_ROLES.includes(chunk.r as TextResponseRole) ||
      !Number.isInteger(chunk.i) ||
      !Number.isInteger(chunk.n) ||
      chunk.i < 1 ||
      chunk.i > chunk.n ||
      chunk.n > this.limits.maxChunksPerMessage ||
      (chunk.u !== undefined && chunk.i !== chunk.n)
    ) {
      return null;
    }

    const key = this.keyFor(chunk);
    const conversationId = normalizeConversationId(chunk.cid);
    let state = this.buffers.get(key);
    if (!state) {
      if (this.buffers.size >= this.limits.maxBuffers) return null;
      state = {
        lastUpdatedAt: this.now(),
        total: chunk.n,
        playerName: chunk.p,
        role: chunk.r as TextResponseRole,
        conversationId,
        title: chunk.t,
        usage: chunk.u,
        byteLength: 0,
        chunks: new Map<number, StoredChunk>(),
      };
      this.buffers.set(key, state);
    } else if (
      state.total !== chunk.n ||
      state.playerName !== chunk.p ||
      state.role !== chunk.r ||
      state.conversationId !== conversationId ||
      state.title !== chunk.t
    ) {
      this.drop(key);
      return null;
    }

    const existing = state.chunks.get(chunk.i);
    if (existing !== undefined) {
      if (existing.content !== chunk.c || !sameUsage(existing.usage, chunk.u)) this.drop(key);
      return null;
    }

    const chunkBytes = utf8ByteLength(chunk.c);
    const nextBytes = state.byteLength + chunkBytes;
    const totalLimit = this.limits.maxTotalBufferBytes ?? Number.POSITIVE_INFINITY;
    if (nextBytes > this.limits.maxMessageBytes || this.totalBufferedBytes + chunkBytes > totalLimit) {
      this.drop(key);
      return null;
    }
    if (chunk.u !== undefined && state.usage !== undefined && !sameUsage(state.usage, chunk.u)) {
      this.drop(key);
      return null;
    }
    state.chunks.set(chunk.i, { content: chunk.c, ...(chunk.u === undefined ? {} : { usage: chunk.u }) });
    state.byteLength = nextBytes;
    state.usage = chunk.u ?? state.usage;
    state.lastUpdatedAt = this.now();
    this.totalBufferedBytes += chunkBytes;
    if (state.chunks.size !== state.total) return null;

    const ordered: string[] = [];
    for (let index = 1; index <= state.total; index += 1) {
      const content = state.chunks.get(index);
      if (content === undefined) return null;
      ordered.push(content.content);
    }
    const result: ReassembledResponse = {
      playerName: state.playerName,
      role: state.role,
      text: ordered.join(""),
      responseId: chunk.id,
      conversationId: state.conversationId,
      ...(state.title === undefined ? {} : { title: state.title }),
      ...(state.usage === undefined ? {} : { usage: state.usage }),
    };
    this.drop(key);
    return result;
  }

  private keyFor(chunk: TextResponseChunk): string {
    // Always include the player and normalized conversation bucket. This
    // prevents two players from colliding when old frames omit cid.
    return `${chunk.p}\u0000${normalizeConversationId(chunk.cid)}\u0000${chunk.id}`;
  }

  private drop(key: string): void {
    const state = this.buffers.get(key);
    if (state) this.totalBufferedBytes -= state.byteLength;
    this.buffers.delete(key);
  }
}

function sameUsage(left: Usage | undefined, right: Usage | undefined): boolean {
  return left?.i === right?.i && left?.o === right?.o;
}

function normalizeConversationId(conversationId: string | undefined): string {
  return conversationId === undefined ? DEFAULT_CONVERSATION_ID : conversationId;
}

// ── Module state ──

let legacyHandler: TextRespHandler | null = null;
let typedHandler: TextResponseMessageHandler | null = null;
let isRegistered = false;
const assembler = new ResponseAssembler();

/** Register the legacy three-argument callback. */
export function setTextRespHandler(fn: TextRespHandler): void {
  legacyHandler = fn;
}

/** Register a typed callback with CID/title/usage metadata. */
export function setTextResponseMessageHandler(fn: TextResponseMessageHandler): void {
  typedHandler = fn;
}

/** Register scriptEventReceive subscription for mcbews:text_resp frames. */
export function registerResponseSyncHandler(): void {
  if (isRegistered) return;
  isRegistered = true;

  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id !== TEXT_RESPONSE_SCRIPT_EVENT_ID) return;
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
    if (result) {
      typedHandler?.(result);
      legacyHandler?.(result.playerName, result.role, result.text);
    }
  });
}

/** @internal */
export function _testingReset(): void {
  legacyHandler = null;
  typedHandler = null;
  isRegistered = false;
  assembler.clear();
}

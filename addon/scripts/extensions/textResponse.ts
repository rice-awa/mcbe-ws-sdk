import { system } from "@minecraft/server";

import {
  RESPONSE_BUFFER_TTL_MS,
  RESPONSE_MAX_BUFFERS,
  RESPONSE_MAX_CHUNKS_PER_MESSAGE,
  RESPONSE_MAX_MESSAGE_BYTES,
  RESPONSE_MAX_TOTAL_BUFFER_BYTES,
  TEXT_RESPONSE_SCRIPT_EVENT_ID,
  TEXT_RESPONSE_ALLOWED_ROLES,
} from "../bridge/protocol";
import { FragmentAssembler } from "../bridge/reassembly";

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
  private readonly core: FragmentAssembler<TextResponseChunk>;

  constructor(
    private readonly limits: ResponseSyncLimits = DEFAULT_RESPONSE_SYNC_LIMITS,
    now: () => number = Date.now
  ) {
    this.core = new FragmentAssembler(limits, now);
  }

  get bufferCount(): number {
    return this.core.bufferCount;
  }
  get bufferedBytes(): number {
    return this.core.bufferedBytes;
  }
  clear(): void {
    this.core.clear();
  }
  pruneExpired(): void {
    this.core.pruneExpired();
  }

  clearForPlayer(playerName: string): void {
    for (const [key, state] of this.core.buffers) {
      if (state.chunks.values().next().value?.item.p === playerName) this.core.drop(key);
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
    )
      return null;
    const conversationId = normalizeConversationId(chunk.cid);
    const key = this.keyFor(chunk);
    const state = this.core.buffers.get(key);
    const first = state?.chunks.values().next().value?.item;
    if (
      first &&
      (first.p !== chunk.p ||
        first.r !== chunk.r ||
        normalizeConversationId(first.cid) !== conversationId ||
        first.t !== chunk.t)
    ) {
      this.core.drop(key);
      return null;
    }
    const duplicate = state?.chunks.get(chunk.i);
    if (duplicate && !sameUsage(duplicate.item.u, chunk.u)) {
      this.core.drop(key);
      return null;
    }
    const complete = this.core.push(key, chunk.i, chunk.n, chunk.c, chunk);
    if (!complete) return null;
    const ordered: TextResponseChunk[] = [];
    for (let index = 1; index <= complete.total; index += 1) {
      const entry = complete.chunks.get(index);
      if (!entry) return null;
      ordered.push(entry.item);
    }
    const usage = ordered[ordered.length - 1].u;
    return {
      playerName: chunk.p,
      role: chunk.r as TextResponseRole,
      text: ordered.map((entry) => entry.c).join(""),
      responseId: chunk.id,
      conversationId,
      ...(chunk.t === undefined ? {} : { title: chunk.t }),
      ...(usage === undefined ? {} : { usage }),
    };
  }

  private keyFor(chunk: TextResponseChunk): string {
    return JSON.stringify([chunk.p, normalizeConversationId(chunk.cid), chunk.id]);
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

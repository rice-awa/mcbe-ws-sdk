import { utf8ByteLength } from "./chunking";

export type ReassemblyLimits = {
  ttlMs: number;
  maxBuffers: number;
  maxChunksPerMessage: number;
  maxMessageBytes: number;
  maxTotalBufferBytes?: number;
};

export type FragmentBuffer<T> = {
  lastUpdatedAt: number;
  total: number;
  byteLength: number;
  chunks: Map<number, { content: string; item: T }>;
};

/** Opaque fragment storage; one instance shares all channel resource budgets. */
export class FragmentAssembler<T> {
  readonly buffers = new Map<string, FragmentBuffer<T>>();
  private totalBufferedBytes = 0;

  constructor(
    private readonly limits: ReassemblyLimits,
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

  drop(key: string): void {
    const state = this.buffers.get(key);
    if (state) this.totalBufferedBytes -= state.byteLength;
    this.buffers.delete(key);
  }

  pruneExpired(now = this.now()): void {
    for (const [key, state] of this.buffers) {
      if (now - state.lastUpdatedAt >= this.limits.ttlMs) this.drop(key);
    }
  }

  push(
    key: string,
    index: number,
    total: number,
    content: string,
    item: T,
    now = this.now()
  ): FragmentBuffer<T> | null {
    this.pruneExpired(now);
    if (
      !Number.isInteger(index) ||
      !Number.isInteger(total) ||
      index < 1 ||
      index > total ||
      total > this.limits.maxChunksPerMessage
    )
      return null;
    let state = this.buffers.get(key);
    if (!state) {
      if (this.bufferCount >= this.limits.maxBuffers) return null;
      state = { lastUpdatedAt: now, total, byteLength: 0, chunks: new Map() };
      this.buffers.set(key, state);
    } else if (state.total !== total) {
      this.drop(key);
      return null;
    }
    const existing = state.chunks.get(index);
    if (existing !== undefined) {
      if (existing.content !== content) this.drop(key);
      return null;
    }
    const bytes = utf8ByteLength(content);
    if (
      state.byteLength + bytes > this.limits.maxMessageBytes ||
      this.bufferedBytes + bytes > (this.limits.maxTotalBufferBytes ?? Number.POSITIVE_INFINITY)
    ) {
      this.drop(key);
      return null;
    }
    state.chunks.set(index, { content, item });
    state.byteLength += bytes;
    state.lastUpdatedAt = now;
    this.totalBufferedBytes += bytes;
    if (state.chunks.size !== state.total) return null;
    this.drop(key);
    return state;
  }
}

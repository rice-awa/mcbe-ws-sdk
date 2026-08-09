import { describe, expect, it, vi, beforeEach } from "vitest";
import { system } from "@minecraft/server";
import {
  ResponseAssembler,
  DEFAULT_RESPONSE_SYNC_LIMITS,
  parseTextResponseChunk,
  setTextRespHandler,
  registerResponseSyncHandler,
  _testingReset,
  type TextResponseChunk,
  type ResponseSyncLimits,
} from "../scripts/bridge/responseSync";
import { chunkPayload, utf8ByteLength } from "../scripts/bridge/chunking";
import { MCBEWS_V1_WIRE_VECTORS } from "../scripts/bridge/protocol";
import { TEXT_RESP_MESSAGE_ID } from "../scripts/bridge/constants";
import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function serverEvent(message: string) {
  return {
    id: TEXT_RESP_MESSAGE_ID,
    message,
    sourceType: "Server" as const,
  };
}

function entityEvent(message: string) {
  return {
    id: TEXT_RESP_MESSAGE_ID,
    message,
    sourceType: "Entity" as const,
  };
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  _testingReset();
  system.afterEvents.scriptEventReceive.clear();
});

// ---------------------------------------------------------------------------
// parseTextResponseChunk
// ---------------------------------------------------------------------------

describe("parseTextResponseChunk", () => {
  it("rejects null", () => {
    expect(parseTextResponseChunk(null)).toBeNull();
  });

  it("rejects non-object primitive", () => {
    expect(parseTextResponseChunk("hello")).toBeNull();
    expect(parseTextResponseChunk(42)).toBeNull();
    expect(parseTextResponseChunk(true)).toBeNull();
  });

  it("rejects array", () => {
    expect(parseTextResponseChunk(["a", "b"])).toBeNull();
  });

  it("rejects missing fields", () => {
    expect(parseTextResponseChunk({ id: "x", i: 1, n: 1, p: "P", r: "user" })).toBeNull();
  });

  it("rejects wrong types", () => {
    expect(parseTextResponseChunk({ id: "x", i: "1", n: 1, p: "P", r: "user", c: "hi" })).toBeNull();
    expect(parseTextResponseChunk({ id: 123, i: 1, n: 1, p: "P", r: "user", c: "hi" })).toBeNull();
    expect(parseTextResponseChunk({ id: "x", i: 1, n: 1, p: 42, r: "user", c: "hi" })).toBeNull();
    expect(parseTextResponseChunk({ id: "x", i: 1, n: 1, p: "P", r: true, c: "hi" })).toBeNull();
    expect(parseTextResponseChunk({ id: "x", i: 1, n: 1, p: "P", r: "user", c: 42 })).toBeNull();
  });

  it("returns a valid chunk for a well-formed object", () => {
    const result = parseTextResponseChunk({ id: "x", i: 1, n: 3, p: "Player1", r: "assistant", c: "hello" });
    expect(result).toEqual({ id: "x", i: 1, n: 3, p: "Player1", r: "assistant", c: "hello" });
  });

  it("accepts cid/title and completion-only usage", () => {
    expect(
      parseTextResponseChunk({
        id: "x",
        i: 1,
        n: 2,
        p: "Player1",
        r: "assistant",
        c: "hello",
        cid: "chat-a",
        t: "Chat",
        u: { i: 1, o: 2 },
      })
    ).toBeNull();
    expect(
      parseTextResponseChunk({
        id: "x",
        i: 2,
        n: 2,
        p: "Player1",
        r: "assistant",
        c: "world",
        cid: "chat-a",
        t: "Chat",
        u: { i: 1, o: 2 },
      })
    ).toEqual({
      id: "x",
      i: 2,
      n: 2,
      p: "Player1",
      r: "assistant",
      c: "world",
      cid: "chat-a",
      t: "Chat",
      u: { i: 1, o: 2 },
    });
    expect(parseTextResponseChunk({ id: "x", i: 1, n: 1, p: "P", r: "unknown", c: "x" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// ResponseAssembler — metadata and limits
// ---------------------------------------------------------------------------

describe("ResponseAssembler", () => {
  it("drops conflicting metadata for the same id", () => {
    const assembler = new ResponseAssembler(DEFAULT_RESPONSE_SYNC_LIMITS, () => 0);
    expect(assembler.push({ id: "x", i: 1, n: 2, p: "A", r: "user", c: "a", t: "one" })).toBeNull();
    expect(assembler.push({ id: "x", i: 2, n: 2, p: "A", r: "user", c: "b", t: "two" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("drops conflicting total for the same id", () => {
    const assembler = new ResponseAssembler(DEFAULT_RESPONSE_SYNC_LIMITS, () => 0);
    expect(assembler.push({ id: "x", i: 1, n: 3, p: "A", r: "user", c: "a" })).toBeNull();
    expect(assembler.push({ id: "x", i: 2, n: 2, p: "A", r: "user", c: "b" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("drops conflicting role for the same id", () => {
    const assembler = new ResponseAssembler(DEFAULT_RESPONSE_SYNC_LIMITS, () => 0);
    expect(assembler.push({ id: "x", i: 1, n: 2, p: "A", r: "user", c: "a" })).toBeNull();
    expect(assembler.push({ id: "x", i: 2, n: 2, p: "A", r: "assistant", c: "b" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("expires stale buffers", () => {
    let now = 0;
    const assembler = new ResponseAssembler(
      {
        ttlMs: 10,
        maxBuffers: DEFAULT_RESPONSE_SYNC_LIMITS.maxBuffers,
        maxChunksPerMessage: DEFAULT_RESPONSE_SYNC_LIMITS.maxChunksPerMessage,
        maxMessageBytes: DEFAULT_RESPONSE_SYNC_LIMITS.maxMessageBytes,
      },
      () => now
    );
    assembler.push({ id: "x", i: 1, n: 2, p: "A", r: "user", c: "a" });
    expect(assembler.bufferCount).toBe(1);
    now = 11;
    assembler.pruneExpired();
    expect(assembler.bufferCount).toBe(0);
  });

  it("reassembles chunks that arrive out of order", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 3, n: 3, p: "P", r: "user", c: "c" })).toBeNull();
    expect(assembler.push({ id: "x", i: 1, n: 3, p: "P", r: "user", c: "a" })).toBeNull();
    const result = assembler.push({ id: "x", i: 2, n: 3, p: "P", r: "user", c: "b" });
    expect(result).toEqual({
      playerName: "P",
      role: "user",
      text: "abc",
      responseId: "x",
      conversationId: "default",
    });
    expect(assembler.bufferCount).toBe(0);
  });

  it("isolates same response ids by player and conversation", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 1, n: 2, p: "A", r: "assistant", c: "a", cid: "one" })).toBeNull();
    expect(assembler.push({ id: "x", i: 1, n: 2, p: "B", r: "assistant", c: "b", cid: "two" })).toBeNull();
    expect(assembler.push({ id: "x", i: 2, n: 2, p: "A", r: "assistant", c: "A", cid: "one" })).toEqual({
      playerName: "A",
      role: "assistant",
      text: "aA",
      responseId: "x",
      conversationId: "one",
    });
    expect(assembler.push({ id: "x", i: 2, n: 2, p: "B", r: "assistant", c: "B", cid: "two" })).toEqual({
      playerName: "B",
      role: "assistant",
      text: "bB",
      responseId: "x",
      conversationId: "two",
    });
  });

  it("isolates same response ids by player when cid is omitted", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "same", i: 1, n: 2, p: "A", r: "assistant", c: "a" })).toBeNull();
    expect(assembler.push({ id: "same", i: 1, n: 2, p: "B", r: "assistant", c: "b" })).toBeNull();
    expect(assembler.push({ id: "same", i: 2, n: 2, p: "A", r: "assistant", c: "A" })).toEqual({
      playerName: "A",
      role: "assistant",
      text: "aA",
      responseId: "same",
      conversationId: "default",
    });
    expect(assembler.push({ id: "same", i: 2, n: 2, p: "B", r: "assistant", c: "B" })).toEqual({
      playerName: "B",
      role: "assistant",
      text: "bB",
      responseId: "same",
      conversationId: "default",
    });
  });

  it("completes on last chunk even if earlier chunks were missing (gaps still return null)", () => {
    const assembler = new ResponseAssembler();
    assembler.push({ id: "x", i: 1, n: 3, p: "P", r: "user", c: "a" });
    assembler.push({ id: "x", i: 3, n: 3, p: "P", r: "user", c: "c" });
    // Only chunks 1 and 3 arrived — size !== total, should return null for now
    expect(assembler.push({ id: "x", i: 2, n: 3, p: "P", r: "user", c: "b" })).toEqual({
      playerName: "P",
      role: "user",
      text: "abc",
      responseId: "x",
      conversationId: "default",
    });
  });

  it("handles identical duplicate chunk idempotently", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 1, n: 2, p: "P", r: "user", c: "a" })).toBeNull();
    // Same chunk again — idempotent
    expect(assembler.push({ id: "x", i: 1, n: 2, p: "P", r: "user", c: "a" })).toBeNull();
    // Buffer still alive
    expect(assembler.bufferCount).toBe(1);
    // Completion should still work
    expect(assembler.push({ id: "x", i: 2, n: 2, p: "P", r: "user", c: "b" })).toEqual({
      playerName: "P",
      role: "user",
      text: "ab",
      responseId: "x",
      conversationId: "default",
    });
  });

  it("accepts final-first identical duplicates including usage metadata", () => {
    const assembler = new ResponseAssembler();
    expect(
      assembler.push({
        id: "dup",
        i: 3,
        n: 3,
        p: "P",
        r: "assistant",
        c: "done",
        cid: "chat-a",
        u: { i: 1, o: 2 },
      })
    ).toBeNull();
    expect(assembler.push({ id: "dup", i: 1, n: 3, p: "P", r: "assistant", c: "answer ", cid: "chat-a" })).toBeNull();
    expect(
      assembler.push({
        id: "dup",
        i: 3,
        n: 3,
        p: "P",
        r: "assistant",
        c: "done",
        cid: "chat-a",
        u: { i: 1, o: 2 },
      })
    ).toBeNull();
    expect(assembler.push({ id: "dup", i: 2, n: 3, p: "P", r: "assistant", c: "", cid: "chat-a" })).toEqual({
      playerName: "P",
      role: "assistant",
      text: "answer done",
      responseId: "dup",
      conversationId: "chat-a",
      usage: { i: 1, o: 2 },
    });
  });

  it("deletes buffer on conflicting duplicate chunk", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 1, n: 2, p: "P", r: "user", c: "a" })).toBeNull();
    // Different content at same index — conflict, buffer deleted
    expect(assembler.push({ id: "x", i: 1, n: 2, p: "P", r: "user", c: "A" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("allows TTL reuse of same id after expiry", () => {
    let now = 0;
    const assembler = new ResponseAssembler(
      {
        ttlMs: 10,
        maxBuffers: DEFAULT_RESPONSE_SYNC_LIMITS.maxBuffers,
        maxChunksPerMessage: DEFAULT_RESPONSE_SYNC_LIMITS.maxChunksPerMessage,
        maxMessageBytes: DEFAULT_RESPONSE_SYNC_LIMITS.maxMessageBytes,
      },
      () => now
    );

    // First buffer with id "x"
    assembler.push({ id: "x", i: 1, n: 2, p: "A", r: "user", c: "a" });
    expect(assembler.bufferCount).toBe(1);

    // Advance past TTL
    now = 11;
    assembler.pruneExpired();
    expect(assembler.bufferCount).toBe(0);

    // New buffer with same id should succeed
    const result = assembler.push({ id: "x", i: 1, n: 2, p: "B", r: "assistant", c: "x" });
    expect(result).toBeNull();
    expect(assembler.bufferCount).toBe(1);

    // Complete it
    expect(assembler.push({ id: "x", i: 2, n: 2, p: "B", r: "assistant", c: "y" })).toEqual({
      playerName: "B",
      role: "assistant",
      text: "xy",
      responseId: "x",
      conversationId: "default",
    });
  });

  it("drops chunk when n exceeds maxChunksPerMessage", () => {
    const assembler = new ResponseAssembler({ ...DEFAULT_RESPONSE_SYNC_LIMITS, maxChunksPerMessage: 2 }, () => 0);
    expect(assembler.push({ id: "x", i: 1, n: 3, p: "P", r: "user", c: "a" })).toBeNull();
    // Chunk is silently dropped — buffer never created
    expect(assembler.bufferCount).toBe(0);
  });

  it("drops new buffer when maxBuffers limit would be exceeded", () => {
    const assembler = new ResponseAssembler({ ...DEFAULT_RESPONSE_SYNC_LIMITS, maxBuffers: 2 }, () => 0);

    // Fill both slots (n=2 so chunks don't complete immediately)
    assembler.push({ id: "a", i: 1, n: 2, p: "P", r: "user", c: "a" });
    assembler.push({ id: "b", i: 1, n: 2, p: "P", r: "user", c: "b" });
    expect(assembler.bufferCount).toBe(2);

    // Third buffer should be dropped
    expect(assembler.push({ id: "c", i: 1, n: 2, p: "P", r: "user", c: "c" })).toBeNull();
    expect(assembler.bufferCount).toBe(2);
  });

  it("drops buffer when total byte length exceeds maxMessageBytes", () => {
    const assembler = new ResponseAssembler({ ...DEFAULT_RESPONSE_SYNC_LIMITS, maxMessageBytes: 10 }, () => 0);

    // 4 bytes each for two 2-byte chars, plus 4 = 8, then next would exceed
    // Actually: "aaaa" = 4 bytes, "bbbb" = 4 bytes, total 8 < 10
    // Next chunk pushes over: "ccccc" = 5 bytes => 13 > 10 → delete
    expect(assembler.push({ id: "x", i: 1, n: 3, p: "P", r: "user", c: "aaaa" })).toBeNull();
    expect(assembler.push({ id: "x", i: 2, n: 3, p: "P", r: "user", c: "bbbb" })).toBeNull();
    // Chunk 3 would push byteLength to 13 > 10 — buffer deleted
    expect(assembler.push({ id: "x", i: 3, n: 3, p: "P", r: "user", c: "ccccc" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("enforces total buffered bytes and clears one player", () => {
    const assembler = new ResponseAssembler({ ...DEFAULT_RESPONSE_SYNC_LIMITS, maxTotalBufferBytes: 3 }, () => 0);
    assembler.push({ id: "a", i: 1, n: 2, p: "A", r: "user", c: "中", cid: "one" });
    expect(assembler.bufferedBytes).toBe(3);
    expect(assembler.push({ id: "b", i: 1, n: 2, p: "B", r: "user", c: "x", cid: "two" })).toBeNull();
    expect(assembler.bufferCount).toBe(1);
    assembler.clearForPlayer("A");
    expect(assembler.bufferCount).toBe(0);
    expect(assembler.bufferedBytes).toBe(0);
  });

  it("clear() empties all buffers", () => {
    const assembler = new ResponseAssembler();
    assembler.push({ id: "a", i: 1, n: 2, p: "P", r: "user", c: "a" });
    assembler.push({ id: "b", i: 1, n: 2, p: "P", r: "user", c: "b" });
    expect(assembler.bufferCount).toBe(2);
    assembler.clear();
    expect(assembler.bufferCount).toBe(0);
  });

  it("rejects chunk with i=0", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 0, n: 1, p: "P", r: "user", c: "a" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("rejects chunk with i > n", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 3, n: 2, p: "P", r: "user", c: "a" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("rejects chunk with non-integer i", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 1.5, n: 2, p: "P", r: "user", c: "a" })).toBeNull();
  });

  it("rejects chunk with non-integer n", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 1, n: 2.5, p: "P", r: "user", c: "a" })).toBeNull();
  });

  it("rejects chunk with empty id", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "", i: 1, n: 1, p: "P", r: "user", c: "a" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("rejects chunk with empty playerName", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 1, n: 1, p: "", r: "user", c: "a" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("rejects chunk with empty role", () => {
    const assembler = new ResponseAssembler();
    expect(assembler.push({ id: "x", i: 1, n: 1, p: "P", r: "", c: "a" })).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("consumes every canonical text behavior vector", () => {
    for (const vector of MCBEWS_V1_WIRE_VECTORS.behavior.textResponse) {
      const assembler = new ResponseAssembler();
      let result = null;
      for (const rawChunk of vector.chunks) {
        result = assembler.push(rawChunk);
      }
      if ("expectedError" in vector) {
        expect(result).toBeNull();
        expect(assembler.bufferCount).toBe(0);
      } else {
        expect(result).toEqual(vector.expected);
        expect(assembler.bufferCount).toBe(0);
      }
    }
  });
});

describe("canonical chunking behavior vectors", () => {
  it("keeps Unicode payloads and empty frames inside the complete wrapper budget", () => {
    for (const vector of MCBEWS_V1_WIRE_VECTORS.behavior.chunking) {
      const wrap = (frame: string) => `${vector.wrapperPrefix}${frame}`;
      if (vector.name === "empty-wrapper-no-room") {
        expect(() =>
          chunkPayload(vector.prefix, vector.id, vector.payload, {
            commandLineByteBudget: vector.budget,
            maxContentCodePoints: vector.maxContentCodePoints,
            wrapCommandLine: wrap,
          })
        ).toThrow("chunk framing leaves no room");
        continue;
      }
      const frames = chunkPayload(vector.prefix, vector.id, vector.payload, {
        commandLineByteBudget: vector.budget,
        maxContentCodePoints: vector.maxContentCodePoints,
        wrapCommandLine: wrap,
      });
      expect(frames.length).toBeGreaterThan(0);
      expect(frames.map((frame) => frame.match(/\|(\d+)\/(\d+)\|([\s\S]*)$/)?.[3] ?? "").join("")).toBe(vector.payload);
      expect(frames.every((frame) => utf8ByteLength(wrap(frame)) <= vector.budget)).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// registerResponseSyncHandler — source acceptance
// ---------------------------------------------------------------------------

describe("registerResponseSyncHandler source acceptance", () => {
  it("accepts Entity source (WS /wsserver commandRequest path)", () => {
    const handler = vi.fn();
    setTextRespHandler(handler);
    registerResponseSyncHandler();

    system.afterEvents.scriptEventReceive.emit(
      entityEvent(JSON.stringify({ id: "x", i: 1, n: 1, p: "P", r: "user", c: "hello" }))
    );

    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith("P", "user", "hello");
  });

  it("processes Server source events", () => {
    const handler = vi.fn();
    setTextRespHandler(handler);
    registerResponseSyncHandler();

    system.afterEvents.scriptEventReceive.emit(
      serverEvent(JSON.stringify({ id: "x", i: 1, n: 1, p: "P", r: "user", c: "hello" }))
    );

    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith("P", "user", "hello");
  });
});

// ---------------------------------------------------------------------------
// registerResponseSyncHandler — malformed input
// ---------------------------------------------------------------------------

describe("registerResponseSyncHandler malformed input", () => {
  it("handles malformed JSON silently (no-op)", () => {
    const handler = vi.fn();
    setTextRespHandler(handler);
    registerResponseSyncHandler();

    system.afterEvents.scriptEventReceive.emit(serverEvent("{{{ invalid json"));

    expect(handler).not.toHaveBeenCalled();
  });

  it("handles missing fields silently (no-op)", () => {
    const handler = vi.fn();
    setTextRespHandler(handler);
    registerResponseSyncHandler();

    system.afterEvents.scriptEventReceive.emit(serverEvent(JSON.stringify({ id: "x", i: 1, n: 1, p: "P" })));

    expect(handler).not.toHaveBeenCalled();
  });

  it("handles array as root silently (no-op)", () => {
    const handler = vi.fn();
    setTextRespHandler(handler);
    registerResponseSyncHandler();

    system.afterEvents.scriptEventReceive.emit(
      serverEvent(JSON.stringify([{ id: "x", i: 1, n: 1, p: "P", r: "user", c: "hello" }]))
    );

    expect(handler).not.toHaveBeenCalled();
  });

  it("handles wrong types silently (no-op)", () => {
    const handler = vi.fn();
    setTextRespHandler(handler);
    registerResponseSyncHandler();

    system.afterEvents.scriptEventReceive.emit(
      serverEvent(JSON.stringify({ id: "x", i: "1", n: 1, p: "P", r: "user", c: "hello" }))
    );

    expect(handler).not.toHaveBeenCalled();
  });
});

import { describe, expect, expectTypeOf, it, vi } from "vitest";
import {
  chunkBridgePayload,
  chunkPayload,
  chunkUiChatPayload,
  formatChunk,
  utf8ByteLength,
} from "../scripts/bridge/chunking";
import type { ChunkOptions } from "../scripts/bridge/chunking";
import {
  BRIDGE_COMMAND_LINE_BYTE_BUDGET,
  BRIDGE_MAX_CHUNK_CONTENT_CODE_POINTS,
} from "../scripts/bridge/constants";

describe("chunking", () => {
  describe("utf8ByteLength", () => {
    it("returns 1 for ASCII", () => {
      expect(utf8ByteLength("a")).toBe(1);
    });

    it("returns 4 for a single emoji", () => {
      expect(utf8ByteLength("😀")).toBe(4);
    });

    it("returns 3 for a single Chinese character", () => {
      expect(utf8ByteLength("中")).toBe(3);
    });

    it("returns correct length for mixed strings", () => {
      // "ab" = 2, "中" = 3 => 5
      expect(utf8ByteLength("ab中")).toBe(5);
    });

    it("works when the Bedrock runtime does not provide TextEncoder", () => {
      vi.stubGlobal("TextEncoder", undefined);
      try {
        expect(utf8ByteLength("A中😀")).toBe(8);
      } finally {
        vi.unstubAllGlobals();
      }
    });

    it("counts malformed UTF-16 surrogates as replacement characters", () => {
      expect(utf8ByteLength("\uD800")).toBe(3);
      expect(utf8ByteLength("\uDC00")).toBe(3);
      expect(utf8ByteLength("\uD800\uD800")).toBe(6);
      expect(utf8ByteLength("\uD83D\uDE00")).toBe(4);
    });
  });

  describe("ChunkOptions type safety", () => {
    it("rejects numeric third argument at type level", () => {
      expectTypeOf(chunkBridgePayload).parameter(2).toMatchTypeOf<ChunkOptions | undefined>();
      expectTypeOf(chunkUiChatPayload).parameter(2).toMatchTypeOf<ChunkOptions | undefined>();
      expectTypeOf(chunkPayload).parameter(3).toMatchTypeOf<ChunkOptions | undefined>();
    });
  });

  describe("chunkBridgePayload", () => {
    it("keeps every final tell command within 461 UTF-8 bytes", () => {
      const chunks = chunkBridgePayload("request-" + "x".repeat(64), "中".repeat(500));
      for (const chunk of chunks) {
        expect(utf8ByteLength(`tell @s ${chunk}`)).toBeLessThanOrEqual(461);
      }
    });

    it("produces chunks whose bare content never exceeds BRIDGE_MAX_CHUNK_CONTENT_CODE_POINTS", () => {
      const chunks = chunkBridgePayload("req-1", "x".repeat(600));
      for (const chunk of chunks) {
        const content = chunk.split("|").slice(4).join("|");
        expect(Array.from(content).length).toBeLessThanOrEqual(BRIDGE_MAX_CHUNK_CONTENT_CODE_POINTS);
      }
    });

    it("handles empty payload", () => {
      const chunks = chunkBridgePayload("req-1", "");
      expect(chunks.length).toBe(1);
      expect(chunks[0]).toContain("MCBEWS|BRIDGE|req-1|1/1|");
    });

    it("handles short payload as a single chunk", () => {
      const chunks = chunkBridgePayload("req-1", "hello");
      expect(chunks.length).toBe(1);
      expect(chunks[0]).toBe("MCBEWS|BRIDGE|req-1|1/1|hello");
    });

    it("round-trips content correctly", () => {
      const payload = "hello world this is a test";
      const chunks = chunkBridgePayload("req-1", payload);
      const contents = chunks.map((chunk) => chunk.split("|").slice(4).join("|"));
      expect(contents.join("")).toBe(payload);
    });

    it("includes correct metadata in each chunk", () => {
      const chunks = chunkBridgePayload("my-id", "abcdef");
      expect(chunks[0]).toMatch(/^MCBEWS\|BRIDGE\|my-id\|1\/\d+\|abcdef$/);
    });

    it("accepts custom commandLineByteBudget", () => {
      const chunks = chunkBridgePayload("req-1", "abc", { commandLineByteBudget: 40 });
      for (const chunk of chunks) {
        expect(utf8ByteLength(`tell @s ${chunk}`)).toBeLessThanOrEqual(40);
      }
    });

    it("accepts custom maxContentCodePoints", () => {
      const chunks = chunkBridgePayload("req-1", "x".repeat(500), { maxContentCodePoints: 32 });
      for (const chunk of chunks) {
        const content = chunk.split("|").slice(4).join("|");
        expect(Array.from(content).length).toBeLessThanOrEqual(32);
      }
    });

    it("accepts custom wrapCommandLine", () => {
      let captured = false;
      const chunks = chunkBridgePayload("req-1", "hello", {
        wrapCommandLine: (chunk: string) => {
          captured = true;
          return `tellraw @a {"rawtext":[{"text":"${chunk}"}]}`;
        },
      });
      expect(captured).toBe(true);
      expect(chunks.length).toBe(1);
    });
  });

  describe("chunkUiChatPayload", () => {
    it("round-trips emoji without isolated surrogates", () => {
      const payload = "x".repeat(255) + "😀中文|pipe\\backslash\nnewline";
      const chunks = chunkUiChatPayload("ui-1", payload);
      const contents = chunks.map((chunk) => chunk.split("|").slice(4).join("|"));
      const content = contents.join("");
      expect(content).toBe(payload);
      expect(contents.some((part) => part.endsWith("\uD83D"))).toBe(false);
      expect(contents.some((part) => part.startsWith("\uDE00"))).toBe(false);
    });

    it("uses BRIDGE_UI_CHAT_PREFIX in chunks", () => {
      const chunks = chunkUiChatPayload("ui-1", "hello");
      expect(chunks[0]).toMatch(/^MCBEWS\|UI_CHAT\|ui-1\|1\/\d+\|hello$/);
    });

    it("handles empty payload", () => {
      const chunks = chunkUiChatPayload("ui-1", "");
      expect(chunks.length).toBe(1);
      expect(chunks[0]).toContain("MCBEWS|UI_CHAT|ui-1|1/1|");
    });
  });

  describe("chunkPayload with custom wrapCommandLine", () => {
    it("uses custom wrapCommandLine for budget checks", () => {
      const chunks = chunkPayload("P", "id", "ab", {
        commandLineByteBudget: 15,
        wrapCommandLine: (chunk: string) => chunk,
      });
      // formatChunk: "P|id|1/1|ab" = 9 bytes; 9 < 15 so single chunk
      expect(chunks.length).toBe(1);
      expect(chunks[0]).toBe("P|id|1/1|ab");
    });
  });

  describe("metadata growth is included in sizing", () => {
    it("produces shorter content when total digits require more framing bytes", () => {
      // Use a very tight budget such that growing from index-digit "9" to "10" changes the frame
      // This test verifies that the fixpoint loop handles metadata growth
      const longId = "id-" + "x".repeat(50);
      const payload = "a".repeat(300);
      const chunks = chunkBridgePayload(longId, payload, { commandLineByteBudget: BRIDGE_COMMAND_LINE_BYTE_BUDGET });
      for (const chunk of chunks) {
        expect(utf8ByteLength(`tell @s ${chunk}`)).toBeLessThanOrEqual(461);
      }
      const contents = chunks.map((chunk) => chunk.split("|").slice(4).join("|"));
      expect(contents.join("")).toBe(payload);
    });
  });

  describe("chunkPayload throws when one symbol exceeds budget", () => {
    it("throws when a single code point plus framing exceeds the budget", () => {
      // With budget of 5 bytes, even "tell @s P|id|1/1|中" is way over
      expect(() =>
        chunkPayload("P", "id", "中", {
          commandLineByteBudget: 5,
        }),
      ).toThrow("chunk framing leaves no room for one Unicode code point");
    });
  });

  describe("formatChunk helper", () => {
    it("formats a chunk with prefix, id, index, total, and content", () => {
      const result = formatChunk("PRE", "id-1", 2, 5, "hello");
      expect(result).toBe("PRE|id-1|2/5|hello");
    });
  });
});

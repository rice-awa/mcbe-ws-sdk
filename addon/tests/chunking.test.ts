import { describe, expect, it, vi } from "vitest";
import { utf8ByteLength } from "../scripts/bridge/chunking";

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
    expect(utf8ByteLength("😀")).toBe(4);
  });
});

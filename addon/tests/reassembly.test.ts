import { describe, expect, it } from "vitest";
import { FragmentAssembler } from "../scripts/bridge/reassembly";

const limits = { ttlMs: 100, maxBuffers: 2, maxChunksPerMessage: 3, maxMessageBytes: 8, maxTotalBufferBytes: 4 };

describe("generic fragment storage", () => {
  it("orders opaque items and shares byte/count quotas across keys", () => {
    const core = new FragmentAssembler<unknown>(limits);
    expect(core.push("a", 2, 2, "中", { arbitrary: 1 })).toBeNull();
    expect(core.push("b", 1, 2, "x", 7)).toBeNull();
    expect(core.push("c", 1, 2, "", null)).toBeNull();
    expect(core.bufferCount).toBe(2);
    expect(core.push("b", 2, 2, "y", 9)).toBeNull();
    expect(core.bufferedBytes).toBe(3);
    const done = core.push("a", 1, 2, "z", { arbitrary: 2 });
    expect(done?.chunks.get(2)?.item).toEqual({ arbitrary: 1 });
    expect(done?.chunks.get(1)?.content).toBe("z");
    expect(core.bufferCount).toBe(0);
    expect(core.bufferedBytes).toBe(0);
  });

  it("drops conflicting duplicates and does not renew duplicate lifetime", () => {
    let now = 0;
    const core = new FragmentAssembler(limits, () => now);
    core.push("a", 1, 2, "x", null);
    now = 90;
    core.push("a", 1, 2, "x", null);
    now = 100;
    core.pruneExpired();
    expect(core.bufferCount).toBe(0);
    core.push("a", 1, 2, "x", null);
    core.push("a", 1, 2, "y", null);
    expect(core.bufferedBytes).toBe(0);
    core.push("a", 1, 2, "x", null);
    core.push("a", 2, 3, "y", null);
    expect(core.bufferCount).toBe(0);
    core.push("a", 1, 2, "x", null);
    core.clear();
    expect(core.bufferedBytes).toBe(0);
  });
});

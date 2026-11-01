import { describe, expect, it } from "vitest";
import { fragment, downstreamCommands, ReassemblyPool, sendUpstream } from "../scripts/bridge/v2/transport";
import { utf8ByteLength } from "../scripts/bridge/chunking";

describe("v2 bounded carrier", () => {
  const message = { v: "MCBEWS/2", kind: "request", id: "p.e.1", method: "echo", params: "玩家😀|\n".repeat(600) };
  it("roundtrips reverse-order downlink under lowered final command budget", () => {
    const commands = downstreamCommands(message, "p.e.2", { command_bytes: 180 });
    expect(commands.length).toBeGreaterThan(2);
    expect(commands.every((v) => utf8ByteLength(v) <= 180)).toBe(true);
    const frames = fragment(message, "p.e.2", "down", { command_bytes: 180 });
    const pool = new ReassemblyPool();
    const values = frames
      .slice()
      .reverse()
      .map((f) => pool.receive("c", "s", f, "down"))
      .filter(Boolean);
    expect(values).toEqual([message]);
    for (const f of frames) expect(pool.receive("c", "s", f, "down")).toBeNull();
    expect(pool.stats).toEqual({ buffers: 0, bytes: 0, tombstones: 1 });
  });
  it("uplink uses independent whole-frame scalar and byte rules", () => {
    const frames = fragment(message, "a.e.2", "up");
    expect(frames.every((v) => Array.from(v).length <= 256 && utf8ByteLength(v) <= 1024)).toBe(true);
    expect(frames.some((v) => utf8ByteLength(v) > 461)).toBe(true);
  });
  it("releases conflict, capacity, cancel and disconnect without cross-source loss", () => {
    let now = 0;
    const pool = new ReassemblyPool({ source_buffers: 1 }, () => now);
    pool.receive("c", "s", "MCBEWS2|a.e.1|1/2|x", "up");
    expect(() => pool.receive("c", "s", "MCBEWS2|a.e.2|1/2|y", "up")).toThrow("LIMIT_EXCEEDED");
    expect(() => pool.receive("c", "s", "MCBEWS2|a.e.1|1/2|z", "up")).toThrow("FRAGMENT_CONFLICT");
    expect(pool.stats.bytes).toBe(0);
    pool.receive("c", "s", "MCBEWS2|a.e.3|1/2|x", "up");
    pool.receive("c2", "s", "MCBEWS2|a.e.3|1/2|x", "up");
    pool.cancel("c", "s", "a.e.3");
    expect(pool.stats.buffers).toBe(1);
    now = 30000;
    pool.prune();
    expect(pool.stats.buffers).toBe(0);
    pool.close("c");
    pool.close("c2");
    expect(pool.stats).toEqual({ buffers: 0, bytes: 0, tombstones: 0 });
  });
  it("does not send remaining frames on sender failure", async () => {
    let count = 0;
    await expect(
      sendUpstream(
        async () => {
          count++;
          throw new Error("broken");
        },
        message,
        "a.e.2"
      )
    ).rejects.toThrow("broken");
    expect(count).toBe(1);
  });
});

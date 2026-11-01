import { expect, it } from "vitest";
import { decode } from "../scripts/bridge/v2/codec";
import { Direction, fragment, Limits, ReassemblyPool } from "../scripts/bridge/v2/transport";

const message = { v: "MCBEWS/2", kind: "notification", method: "echo", params: "x".repeat(500) };

for (const [direction, tid, other] of [
  ["up", "a.e.1", "p.e.1"],
  ["down", "p.e.1", "a.e.1"],
] as [Direction, string, string][]) {
  it.each(["2/3" + "x".repeat(500), "2/" + String.fromCharCode(0xd800), "bad-index"])(
    `releases ${direction} continuation without content separator %j`,
    (tail) => {
      const pool = new ReassemblyPool();
      const frames = fragment(message, tid, direction);
      expect(pool.receive("c", "s", frames[0], direction)).toBeNull();
      expect(pool.stats.bytes).toBeGreaterThan(0);
      expect(() => pool.receive("c", "s", `MCBEWS2|${tid}|${tail}`, direction)).toThrow("INVALID_FRAGMENT");
      expect(pool.stats).toEqual({ buffers: 0, bytes: 0, tombstones: 1 });
      for (const f of frames.slice(1)) expect(pool.receive("c", "s", f, direction)).toBeNull();
    }
  );

  it(`does not allocate for unattributable or unknown invalid ${direction} frames`, () => {
    const pool = new ReassemblyPool({ tombstones: 1 });
    const invalid = [
      `wrong|${tid}|bad-index`,
      `MCBEWS2|${tid}`,
      `MCBEWS2|${other}|bad-index`,
      `MCBEWS2|${tid}!|bad-index`,
      `MCBEWS2|${tid.slice(0, -1)}2|bad-index`,
      `MCBEWS2|${tid.slice(0, -1)}2|2/3` + "x".repeat(500),
      `MCBEWS2|${tid.slice(0, -1)}2|2/` + String.fromCharCode(0xd800),
    ];
    for (const frame of invalid) {
      expect(() => pool.receive("c", "s", frame, direction)).toThrow("INVALID_FRAGMENT");
      expect(pool.stats).toEqual({ buffers: 0, bytes: 0, tombstones: 0 });
    }
    const frames = fragment(message, tid, direction);
    expect(pool.receive("c", "s", frames[0], direction)).toBeNull();
    const active = pool.stats;
    for (const frame of invalid) {
      expect(() => pool.receive("c", "s", frame, direction)).toThrow("INVALID_FRAGMENT");
      expect(pool.stats).toEqual(active);
    }
    expect(
      frames
        .slice(1)
        .map((f) => pool.receive("c", "s", f, direction))
        .filter(Boolean)
    ).toEqual([message]);
    expect(pool.stats).toEqual({ buffers: 0, bytes: 0, tombstones: 1 });
  });
}

it("samples once across prune/push and suppresses expiry without ghost owners", () => {
  let now = 0;
  let advancing = false;
  let calls = 0;
  const pool = new ReassemblyPool({ tombstones: 2 }, () => (advancing ? 29999 + calls++ : now));
  const a = fragment(message, "a.e.1", "up");
  pool.receive("c", "s", a[0], "up");
  advancing = true;
  pool.receive("c", "s", "MCBEWS2|a.e.2|1/2|x", "up");
  expect(calls).toBe(1);
  advancing = false;
  now = 30000;
  pool.prune();
  for (const frame of a) expect(pool.receive("c", "s", frame, "up")).toBeNull();
  expect(pool.stats.tombstones).toBe(1);
  now = 60000;
  pool.prune();
  now = 90000;
  pool.prune();
  for (const tid of ["a.e.3", "a.e.4"]) {
    const frames = fragment(message, tid, "up");
    expect(frames.map((f) => pool.receive("c", "s", f, "up")).filter(Boolean)).toEqual([message]);
  }
});

it.each(["x".repeat(300), String.fromCharCode(0xd800)])("releases invalid continuation %j", (bad) => {
  const pool = new ReassemblyPool();
  const frames = fragment(message, "a.e.1", "up");
  pool.receive("c", "s", frames[0], "up");
  expect(() => pool.receive("c", "s", "MCBEWS2|a.e.1|2/3|" + bad, "up")).toThrow("INVALID_FRAGMENT");
  expect(pool.stats).toEqual({ buffers: 0, bytes: 0, tombstones: 1 });
  for (const f of frames) expect(pool.receive("c", "s", f, "up")).toBeNull();
});

it.each<{ limits: Limits; extra: Record<string, unknown> }>([
  { limits: { metadata_bytes: 1 }, extra: { meta: { x: "y" } } },
  { limits: { depth: 1 }, extra: { params: { nested: [0] } } },
])("enforces logical limits %j", ({ limits, extra }) => {
  const value = { ...message, ...extra };
  expect(() => fragment(value, "a.e.1", "up", limits)).toThrow("LIMIT_EXCEEDED");
  const pool = new ReassemblyPool(limits);
  const frames = fragment(value, "a.e.1", "up");
  for (const f of frames.slice(0, -1)) expect(pool.receive("c", "s", f, "up")).toBeNull();
  expect(() => pool.receive("c", "s", frames[frames.length - 1], "up")).toThrow("LIMIT_EXCEEDED");
  expect(pool.stats).toEqual({ buffers: 0, bytes: 0, tombstones: 1 });
});

it.each([29999, 30000, 30001])("honors nonzero exact TTL and replay at %i", (elapsed) => {
  let now = 2001;
  const pool = new ReassemblyPool({}, () => now);
  const frames = fragment(message, "a.e.1", "up");
  for (const f of frames.slice(0, -1)) pool.receive("c", "s", f, "up");
  now += elapsed;
  expect(pool.receive("c", "s", frames[frames.length - 1], "up")).toEqual(elapsed < 30000 ? message : null);
  for (const f of frames) expect(pool.receive("c", "s", f, "up")).toBeNull();
});

it("classifies raw huge integers and preserves parsed IEEE numeric semantics", () => {
  const wire = (token: string) => '{"v":"MCBEWS/2","kind":"notification","method":"echo","params":' + token + "}";
  expect(() => decode(wire("9".repeat(5000)))).toThrow("INVALID_MESSAGE");
  expect(decode(wire("1.0000000000000001")).params).toBe(1);
  expect(decode(wire("1e-4000")).params).toBe(0);
});

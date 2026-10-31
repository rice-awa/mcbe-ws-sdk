import { describe, expect, it } from "vitest";
import { CapabilityRegistry } from "../scripts/bridge/capabilityRegistry";
import { V2Endpoint } from "../scripts/bridge/v2/runtime";
import { fragment, ReassemblyPool } from "../scripts/bridge/v2/transport";

describe("v2 runtime", () => {
  it("pairs requests and dispatches reverse requests with metadata", async () => {
    const registry = new CapabilityRegistry();
    const frames: string[] = [];
    const endpoint = new V2Endpoint({
      epoch: "test",
      registry,
      send: async (frame) => {
        frames.push(frame);
      },
      timeout: () => () => {},
    });
    const meta = { extra: { list: [null, "玩家", { flag: true }] } };
    registry.register("echo", async (_name, payload, context) => {
      expect(context.metadata).toEqual(meta);
      expect(context.caller).toEqual({ kind: "server", source: "Entity" });
      return { echo: payload };
    });
    const request = {
      v: "MCBEWS/2",
      kind: "request",
      id: "p.test.1",
      method: "echo",
      params: { answer: 42 },
      meta,
    };
    for (const frame of fragment(request, "p.test.2", "down")) await endpoint.receive(frame, "Entity");
    await endpoint.drain();
    const pool = new ReassemblyPool();
    const response = frames.map((frame) => pool.receive("c", "s", frame, "up")).find((value) => value !== null);
    expect(response).toEqual({
      v: "MCBEWS/2",
      kind: "response",
      id: "p.test.1",
      result: { echo: { answer: 42 } },
      meta,
    });

    frames.length = 0;
    const pending = endpoint.request("reverse", { question: true }, { metadata: meta, traceId: "trace_1" });
    await Promise.resolve();
    const outgoing = frames.map((frame) => pool.receive("c", "s", frame, "up")).find((value) => value !== null);
    expect(outgoing?.method).toBe("reverse");
    const reply = { v: "MCBEWS/2", kind: "response", id: outgoing!.id, result: 7, meta: outgoing!.meta };
    for (const frame of fragment(reply, "p.test.3", "down")) await endpoint.receive(frame, "Entity");
    expect((await pending).result).toBe(7);
    endpoint.close();
  });

  it("releases pending calls on close and bounds active sends", async () => {
    const registry = new CapabilityRegistry();
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    const endpoint = new V2Endpoint({
      epoch: "test",
      registry,
      maxPending: 1,
      maxActive: 1,
      send: async () => blocked,
      timeout: () => () => {},
    });
    const pending = endpoint.request("one", {});
    await expect(endpoint.request("two", {})).rejects.toThrow("pending limit");
    endpoint.close();
    await expect(pending).rejects.toThrow("closed");
    release();
  });
});

import { describe, expect, it } from "vitest";
import { utf8ByteLength } from "../scripts/bridge/chunking";
import { CapabilityRegistry } from "../scripts/bridge/capabilityRegistry";
import { Message } from "../scripts/bridge/v2/codec";
import { V2Endpoint } from "../scripts/bridge/v2/runtime";
import { fragment, ReassemblyPool } from "../scripts/bridge/v2/transport";

async function call(
  registry: CapabilityRegistry,
  method: string,
  params: Record<string, unknown> = {}
): Promise<Message> {
  const frames: string[] = [];
  const endpoint = new V2Endpoint({
    epoch: "errors",
    registry,
    send: async (frame) => {
      frames.push(frame);
    },
    timeout: () => () => {},
  });
  const request = { v: "MCBEWS/2", kind: "request", id: "p.errors.1", method, params };
  for (const frame of fragment(request, "p.errors.2", "down")) await endpoint.receive(frame, "Entity");
  await endpoint.drain();
  const pool = new ReassemblyPool();
  const response = frames.map((frame) => pool.receive("c", "s", frame, "up")).find((value) => value !== null);
  endpoint.close();
  if (!response) throw new Error("no response");
  return response;
}

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

describe("v2 error fidelity", () => {
  it("reports an unregistered capability with its real code and message", async () => {
    const response = await call(new CapabilityRegistry(), "missing");
    expect(response.error).toEqual({
      code: "UNSUPPORTED_METHOD",
      message: "unsupported capability: missing",
    });
  });

  it("passes through a validated parameter message", async () => {
    const registry = new CapabilityRegistry();
    registry.register("validated", () => ({ ok: true }), {
      validate: () => "payload.target must be a string",
    });
    const response = await call(registry, "validated");
    expect(response.error).toEqual({
      code: "INVALID_PARAMS",
      message: "payload.target must be a string",
    });
  });

  it("passes through a bounded handler failure message", async () => {
    const registry = new CapabilityRegistry();
    const detail = "内部失败😀".repeat(200);
    registry.register("failing", () => ({ ok: false, error: { code: "CAPABILITY_FAILED", message: detail } }));
    const response = await call(registry, "failing");
    const error = response.error as { code: string; message: string };
    expect(error.code).toBe("HANDLER_FAILED");
    expect(error.message.length).toBeGreaterThan(0);
    expect(utf8ByteLength(error.message)).toBeLessThanOrEqual(256);
    expect(detail.startsWith(error.message)).toBe(true);
    expect(error.message).not.toContain("at ");
  });

  it("never emits a lone surrogate when truncating", async () => {
    const registry = new CapabilityRegistry();
    registry.register("broken", () => ({
      ok: false,
      error: { code: "CAPABILITY_FAILED", message: "x".repeat(255) + "\uD800tail" },
    }));
    const response = await call(registry, "broken");
    expect((response.error as { message: string }).message).toBe("x".repeat(255));
  });

  it("includes the remote code and bounded message for a failed reverse call", async () => {
    const frames: string[] = [];
    const endpoint = new V2Endpoint({
      epoch: "reverse",
      registry: new CapabilityRegistry(),
      send: async (frame) => {
        frames.push(frame);
      },
      timeout: () => () => {},
    });
    const pending = endpoint.request("remote", {});
    await Promise.resolve();
    const outgoing = new ReassemblyPool().receive("c", "s", frames[0], "up");
    const reply = {
      v: "MCBEWS/2",
      kind: "response",
      id: outgoing!.id,
      error: { code: "TIMEOUT", message: "remote busy" },
    };
    for (const frame of fragment(reply, "p.reverse.1", "down")) await endpoint.receive(frame, "Entity");
    await expect(pending).rejects.toThrow("TIMEOUT: remote busy");
    endpoint.close();
  });
});

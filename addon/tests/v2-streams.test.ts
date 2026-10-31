import { afterEach, describe, expect, it, vi } from "vitest";
import { CapabilityRegistry, V2RequestCancellation } from "../scripts/bridge/capabilityRegistry";
import { MANIFEST } from "../scripts/bridge/v2/assets";
import { Message } from "../scripts/bridge/v2/codec";
import { V2Endpoint } from "../scripts/bridge/v2/runtime";
import { StreamError, StreamLimits } from "../scripts/bridge/v2/streams";
import { fragment, ReassemblyPool } from "../scripts/bridge/v2/transport";

const endpoints: V2Endpoint[] = [];
afterEach(async () => {
  for (const endpoint of endpoints.splice(0)) {
    endpoint.close();
    await endpoint.drain();
  }
  vi.useRealTimers();
});

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

function harness(streamLimits?: Partial<StreamLimits>, features?: string[]) {
  const messages: Message[] = [];
  const frames: string[] = [];
  const pool = new ReassemblyPool();
  let nextId = 0;
  const endpoint = new V2Endpoint({
    epoch: "test",
    registry: new CapabilityRegistry(),
    streamLimits,
    features,
    send: async (frame) => {
      frames.push(frame);
      const message = pool.receive("peer", "addon", frame, "up");
      if (message) messages.push(message);
    },
    timeout: (callback, milliseconds = 5000) => {
      const handle = setTimeout(callback, milliseconds);
      return () => clearTimeout(handle);
    },
  });
  endpoints.push(endpoint);
  const receive = async (message: Message) => {
    for (const frame of fragment({ v: "MCBEWS/2", ...message }, `p.test.${++nextId}`, "down")) {
      await endpoint.receive(frame, "Entity");
    }
  };
  return { endpoint, receive, messages, frames };
}

async function settle() {
  for (let step = 0; step < 15; step += 1) await Promise.resolve();
}

describe("v2 real streams", () => {
  it("delivers first item before the producer can finish, with independent request and streams", async () => {
    const { endpoint, receive, messages } = harness();
    const release = deferred();
    const firstProduced = deferred();
    const contexts: unknown[] = [];
    endpoint.registerStream("numbers", async function* (_name, params, context) {
      contexts.push(context.metadata);
      yield params.first;
      firstProduced.resolve();
      await release.promise;
      yield params.second;
    });
    await receive({
      kind: "stream_open",
      id: "p.test.101",
      method: "numbers",
      params: { first: 1, second: 2 },
      meta: { extra: "preserved" },
    });
    await firstProduced.promise;
    expect(messages).toContainEqual({ v: "MCBEWS/2", kind: "stream_item", id: "p.test.101", seq: 0, item: 1 });
    expect(messages.some((message) => message.kind === "stream_end")).toBe(false);
    await receive({ kind: "stream_open", id: "p.test.102", method: "numbers", params: { first: "x", second: "y" } });
    const incoming = await endpoint.stream("remote", {});
    const request = endpoint.request("echo", {});
    await settle();
    const outgoingRequest = messages.find((message) => message.kind === "request")!;
    await receive({ kind: "response", id: outgoingRequest.id, result: "request-done" });
    expect((await request).result).toBe("request-done");
    await receive({ kind: "stream_item", id: incoming.id, seq: 0, item: "remote-first" });
    expect(await incoming.next()).toEqual({ done: false, value: "remote-first" });
    release.resolve();
    await endpoint.drain();
    expect(messages.filter((message) => message.kind === "stream_end").map((message) => message.id)).toEqual([
      "p.test.101",
      "p.test.102",
    ]);
    expect(contexts[0]).toEqual({ extra: "preserved" });
    await receive({ kind: "stream_end", id: incoming.id, seq: 1 });
    expect(await incoming.next()).toEqual({ done: true, value: undefined });
    expect(endpoint.streamStats.active).toBe(0);
  });

  it("reassembles a large logical item once and handles an empty stream", async () => {
    const { endpoint, receive, messages, frames } = harness();
    endpoint.registerStream("large", async function* () {
      yield "玩家😀".repeat(1200);
    });
    endpoint.registerStream("empty", async function* () {});
    await receive({ kind: "stream_open", id: "p.test.101", method: "large", params: {} });
    await receive({ kind: "stream_open", id: "p.test.102", method: "empty", params: {} });
    await endpoint.drain();
    expect(frames.length).toBeGreaterThan(3);
    expect(messages.filter((message) => message.kind === "stream_item")).toEqual([
      { v: "MCBEWS/2", kind: "stream_item", id: "p.test.101", seq: 0, item: "玩家😀".repeat(1200) },
    ]);
    expect(messages.filter((message) => message.kind === "stream_end")).toContainEqual({
      v: "MCBEWS/2",
      kind: "stream_end",
      id: "p.test.102",
      seq: 0,
    });
    const incoming = await endpoint.stream("large", {});
    await receive({ kind: "stream_item", id: incoming.id, seq: 0, item: "中😀".repeat(1000) });
    await receive({ kind: "stream_end", id: incoming.id, seq: 1 });
    expect(await incoming.next()).toEqual({ done: false, value: "中😀".repeat(1000) });
    expect(await incoming.next()).toEqual({ done: true, value: undefined });
  });

  it("fails sequence gaps and duplicate terminal frames without reviving streams", async () => {
    const { endpoint, receive, messages } = harness();
    const stream = await endpoint.stream("remote", {});
    await receive({ kind: "stream_item", id: stream.id, seq: 1, item: "missing-zero" });
    await expect(stream.next()).rejects.toMatchObject({ code: "INVALID_MESSAGE" });
    await receive({ kind: "stream_item", id: stream.id, seq: 0, item: "late" });
    await receive({ kind: "stream_end", id: stream.id, seq: 0 });
    await expect(stream.next()).rejects.toMatchObject({ code: "INVALID_MESSAGE" });
    expect(endpoint.streamStats.active).toBe(0);
    expect(messages.filter((message) => message.kind === "stream_cancel")).toHaveLength(1);
    const empty = await endpoint.stream("remote", {});
    await receive({ kind: "stream_end", id: empty.id, seq: 0 });
    await receive({ kind: "stream_error", id: empty.id, seq: 0, error: { code: "HANDLER_FAILED", message: "late" } });
    expect(await empty.next()).toEqual({ done: true, value: undefined });
  });

  it("bounds paused consumers by count and bytes and isolates other streams", async () => {
    const { endpoint, receive, messages } = harness({ buffer_items: 1, buffer_bytes: 12, connection_bytes: 16 });
    const first = await endpoint.stream("remote", {});
    const second = await endpoint.stream("remote", {});
    await receive({ kind: "stream_item", id: first.id, seq: 0, item: "one" });
    expect(endpoint.streamStats).toMatchObject({ bufferedItems: 1, bufferedBytes: 5 });
    await receive({ kind: "stream_item", id: first.id, seq: 1, item: "overflow" });
    await expect(first.next()).rejects.toMatchObject({ code: "LIMIT_EXCEEDED" });
    await receive({ kind: "stream_item", id: second.id, seq: 0, item: "ok" });
    expect(await second.next()).toEqual({ done: false, value: "ok" });
    await receive({ kind: "stream_item", id: second.id, seq: 1, item: "byte-limit-exceeded" });
    await expect(second.next()).rejects.toMatchObject({ code: "LIMIT_EXCEEDED" });
    expect(endpoint.streamStats).toMatchObject({ active: 0, bufferedItems: 0, bufferedBytes: 0 });
    expect(messages.filter((message) => message.kind === "stream_cancel")).toHaveLength(2);
  });

  it("cancels producers cooperatively, ignores duplicate opens, and unregisters captured handlers", async () => {
    const { endpoint, receive, messages } = harness();
    const stopped = deferred();
    const started = deferred();
    let calls = 0;
    const unregister = endpoint.registerStream("waiting", async function* (_method, _params, context) {
      calls += 1;
      try {
        yield "first";
        started.resolve();
        await new Promise<void>((resolve) => context.cancellation!.onCancel(resolve));
      } finally {
        stopped.resolve();
      }
    });
    await receive({ kind: "stream_open", id: "p.test.101", method: "waiting", params: {} });
    await started.promise;
    unregister();
    await receive({ kind: "stream_cancel", id: "p.test.101" });
    await stopped.promise;
    await endpoint.drain();
    await receive({ kind: "stream_open", id: "p.test.101", method: "waiting", params: {} });
    await endpoint.drain();
    expect(calls).toBe(1);
    expect(messages.filter((message) => message.kind === "stream_item")).toHaveLength(1);
    expect(messages.some((message) => message.kind === "stream_end")).toBe(false);
    expect(endpoint.streamStats).toMatchObject({ active: 0, bufferedItems: 0, bufferedBytes: 0, tasks: 0 });
  });

  it("handles consumer return, cancellation handle, close, failure, and idle timeout", async () => {
    vi.useFakeTimers();
    const { endpoint, receive, messages } = harness({ idle_ms: 100 });
    const cancellation = new V2RequestCancellation();
    const one = await endpoint.stream("remote", {}, { cancellation });
    const waiter = one.next();
    const checkCancelled = expect(waiter).rejects.toMatchObject({ code: "CANCELLED" });
    cancellation.cancel();
    await checkCancelled;
    await one.return();
    const timeout = await endpoint.stream("remote", {});
    const checkTimedOut = expect(timeout.next()).rejects.toMatchObject({ code: "TIMEOUT" });
    await vi.advanceTimersByTimeAsync(100);
    await checkTimedOut;
    endpoint.registerStream("failure", async function* () {
      throw new Error("private");
    });
    await receive({ kind: "stream_open", id: "p.test.101", method: "failure", params: {} });
    await endpoint.drain();
    expect(messages).toContainEqual({
      v: "MCBEWS/2",
      kind: "stream_error",
      id: "p.test.101",
      seq: 0,
      error: { code: "HANDLER_FAILED", message: "HANDLER_FAILED" },
    });
    const closed = await endpoint.stream("remote", {});
    await receive({ kind: "stream_item", id: closed.id, seq: 0, item: "buffered" });
    endpoint.close();
    await expect(closed.next()).rejects.toMatchObject({ code: "CANCELLED" });
    expect(endpoint.streamStats).toMatchObject({ active: 0, bufferedBytes: 0 });
    expect(messages.filter((message) => message.kind === "stream_cancel")).toHaveLength(2);
  });

  it("enforces the connection byte bound and retains admission for completed buffered streams", async () => {
    const { endpoint, receive } = harness({ max_active: 2, buffer_bytes: 16, connection_bytes: 10 });
    const first = await endpoint.stream("remote", {});
    const second = await endpoint.stream("remote", {});
    await receive({ kind: "stream_item", id: first.id, seq: 0, item: "12345" });
    await receive({ kind: "stream_end", id: first.id, seq: 1 });
    await expect(endpoint.stream("remote", {})).rejects.toMatchObject({ code: "LIMIT_EXCEEDED" });
    await receive({ kind: "stream_item", id: second.id, seq: 0, item: "123" });
    await expect(second.next()).rejects.toMatchObject({ code: "LIMIT_EXCEEDED" });
    expect(endpoint.streamStats.bufferedBytes).toBe(7);
    expect(await first.next()).toEqual({ done: false, value: "12345" });
    expect(endpoint.streamStats).toMatchObject({ active: 0, bufferedBytes: 0 });
  });

  it("stops the remainder of a fragmented producer item after cancellation", async () => {
    const entered = deferred();
    const release = deferred();
    const frames: string[] = [];
    const endpoint = new V2Endpoint({
      epoch: "test",
      registry: new CapabilityRegistry(),
      timeout: () => () => {},
      send: async (frame) => {
        frames.push(frame);
        entered.resolve();
        await release.promise;
      },
    });
    endpoints.push(endpoint);
    let finalized = false;
    endpoint.registerStream("large", async function* () {
      try {
        yield "中😀".repeat(2000);
      } finally {
        finalized = true;
      }
    });
    for (const frame of fragment(
      { v: "MCBEWS/2", kind: "stream_open", id: "p.test.101", method: "large", params: {} },
      "p.test.1",
      "down"
    )) {
      await endpoint.receive(frame, "Entity");
    }
    await entered.promise;
    for (const frame of fragment({ v: "MCBEWS/2", kind: "stream_cancel", id: "p.test.101" }, "p.test.2", "down")) {
      await endpoint.receive(frame, "Entity");
    }
    await endpoint.drain();
    release.resolve();
    await settle();
    expect(frames).toHaveLength(1);
    expect(finalized).toBe(true);
    expect(endpoint.streamStats).toMatchObject({ active: 0, tasks: 0 });
  });

  it("does not let a stale unregister remove a replacement using the same handler", async () => {
    const { endpoint, receive, messages } = harness();
    const handler = async function* () {
      yield "replacement";
    };
    const stale = endpoint.registerStream("test", handler);
    stale();
    endpoint.registerStream("test", handler);
    stale();
    await receive({ kind: "stream_open", id: "p.test.101", method: "test", params: {} });
    await endpoint.drain();
    expect(messages.find((message) => message.kind === "stream_item")?.item).toBe("replacement");
  });

  it("times out an unfinished producer and rejects unsupported streaming", async () => {
    vi.useFakeTimers();
    const { endpoint, receive, messages } = harness({ idle_ms: 100 });
    endpoint.registerStream("idle", async function* (_method, _params, context) {
      await new Promise<void>((resolve) => context.cancellation!.onCancel(resolve));
      yield "late";
    });
    await receive({ kind: "stream_open", id: "p.test.101", method: "idle", params: {} });
    await vi.advanceTimersByTimeAsync(100);
    await endpoint.drain();
    expect(messages).toContainEqual({
      v: "MCBEWS/2",
      kind: "stream_error",
      id: "p.test.101",
      seq: 0,
      error: { code: "TIMEOUT", message: "TIMEOUT" },
    });
    expect(endpoint.streamStats.active).toBe(0);
    const legacy = harness(
      undefined,
      MANIFEST.negotiation.features.filter((feature) => feature !== "stream/1")
    ).endpoint;
    await expect(legacy.stream("remote", {})).rejects.toThrow("unsupported required features: stream/1");
    expect(new StreamError("CANCELLED").code).toBe("CANCELLED");
  });
});

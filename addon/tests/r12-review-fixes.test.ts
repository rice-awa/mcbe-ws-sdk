import { afterEach, describe, expect, it, vi } from "vitest";
import { type Player, world } from "@minecraft/server";
import { createDefaultCapabilityRegistry, handleGetInventorySnapshot } from "../scripts/bridge/capabilities";
import { CapabilityRegistry, V2RequestCancellation } from "../scripts/bridge/capabilityRegistry";
import { V2Endpoint } from "../scripts/bridge/v2/runtime";
import { fragment, ReassemblyPool } from "../scripts/bridge/v2/transport";

afterEach(() => {
  world.getAllPlayers.mockReset().mockReturnValue([]);
});

describe("v2 built-in inventory", () => {
  it("returns unnamed and named items while preserving the v1 JSON shape", async () => {
    const items = [
      { typeId: "minecraft:stone", amount: 1 },
      { typeId: "minecraft:diamond", amount: 2, nameTag: "Treasure" },
      { typeId: "minecraft:stick", amount: 3, nameTag: "" },
    ];
    world.getAllPlayers.mockReturnValue([
      {
        name: "Alex",
        getComponent: () => ({ container: { size: items.length, getItem: (slot: number) => items[slot] } }),
      } as unknown as Player,
    ]);
    const expected = {
      ok: true,
      payload: {
        inventories: [
          {
            player: "Alex",
            size: 3,
            items: [
              { slot: 0, typeId: "minecraft:stone", amount: 1 },
              { slot: 1, typeId: "minecraft:diamond", amount: 2, nameTag: "Treasure" },
              { slot: 2, typeId: "minecraft:stick", amount: 3, nameTag: "" },
            ],
          },
        ],
      },
    };
    expect(JSON.parse(JSON.stringify(handleGetInventorySnapshot({})))).toEqual(expected);

    const frames: string[] = [];
    const endpoint = new V2Endpoint({
      epoch: "inventory",
      registry: createDefaultCapabilityRegistry(),
      send: async (frame) => {
        frames.push(frame);
      },
      timeout: () => () => {},
    });
    const request = {
      v: "MCBEWS/2",
      kind: "request",
      id: "p.inventory.1",
      method: "get_inventory_snapshot",
      params: {},
    };
    for (const frame of fragment(request, "p.inventory.2", "down")) await endpoint.receive(frame, "Entity");
    await endpoint.drain();
    const pool = new ReassemblyPool();
    const response = frames.map((frame) => pool.receive("c", "s", frame, "up")).find((value) => value !== null);
    expect(response).toEqual({ v: "MCBEWS/2", kind: "response", id: "p.inventory.1", result: expected, meta: {} });
    endpoint.close();
  });

  it("still rejects undefined values returned by custom handlers", async () => {
    const registry = new CapabilityRegistry();
    registry.register("invalid", () => ({ value: undefined }));
    const frames: string[] = [];
    const endpoint = new V2Endpoint({
      epoch: "strict",
      registry,
      send: async (frame) => {
        frames.push(frame);
      },
      timeout: () => () => {},
    });
    const request = { v: "MCBEWS/2", kind: "request", id: "p.strict.1", method: "invalid", params: {} };
    for (const frame of fragment(request, "p.strict.2", "down")) await endpoint.receive(frame, "Entity");
    await endpoint.drain();
    const pool = new ReassemblyPool();
    const response = frames.map((frame) => pool.receive("c", "s", frame, "up")).find((value) => value !== null);
    expect(response?.error).toEqual({ code: "HANDLER_FAILED", message: "invalid handler result" });
    endpoint.close();
  });
});

describe("v2 request cancellation", () => {
  it("cancels one request and its remaining fragments while another request completes", async () => {
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    const frames: string[] = [];
    const timers = new Set<() => void>();
    const endpoint = new V2Endpoint({
      epoch: "cancel",
      registry: new CapabilityRegistry(),
      maxPending: 2,
      send: async (frame) => {
        frames.push(frame);
        if (frames.length === 1) await blocked;
      },
      timeout: (callback) => {
        timers.add(callback);
        return () => timers.delete(callback);
      },
    });
    const cancellation = new V2RequestCancellation();
    const pending = endpoint.request("large", { text: "x".repeat(2000) }, { cancellation });
    const cancelledResult = expect(pending).rejects.toThrow("cancelled");
    const other = endpoint.request("other", {});
    expect(frames).toHaveLength(2);
    const firstTransport = frames[0].split("|")[1];
    const outgoing = new ReassemblyPool().receive("c", "s", frames[1], "up");
    expect(outgoing?.method).toBe("other");

    cancellation.cancel();
    await cancelledResult;
    expect(timers.size).toBe(1);
    const cancelledId = /"id":"([^"]+)"/.exec(frames[0])?.[1];
    const lateReply = { v: "MCBEWS/2", kind: "response", id: cancelledId, result: "late" };
    for (const frame of fragment(lateReply, "p.cancel.3", "down")) await endpoint.receive(frame, "Entity");
    const replacement = endpoint.request("replacement", {});
    const replacementOutgoing = new ReassemblyPool().receive("c", "s", frames[2], "up");
    expect(replacementOutgoing?.method).toBe("replacement");
    await expect(endpoint.request("overflow", {})).rejects.toThrow("pending limit");
    release();
    await Promise.resolve();
    await Promise.resolve();
    expect(frames.filter((frame) => frame.split("|")[1] === firstTransport)).toHaveLength(1);

    const reply = { v: "MCBEWS/2", kind: "response", id: outgoing!.id, result: "ok" };
    for (const frame of fragment(reply, "p.cancel.1", "down")) await endpoint.receive(frame, "Entity");
    expect((await other).result).toBe("ok");
    const replacementReply = { v: "MCBEWS/2", kind: "response", id: replacementOutgoing!.id, result: "new" };
    for (const frame of fragment(replacementReply, "p.cancel.2", "down")) await endpoint.receive(frame, "Entity");
    expect((await replacement).result).toBe("new");
    expect(timers.size).toBe(0);
    endpoint.close();
  });

  it("rejects an already cancelled request without sending or registering a timer", async () => {
    const cancellation = new V2RequestCancellation();
    cancellation.cancel();
    const send = vi.fn(async (_frame: string) => {});
    const timeout = vi.fn(() => () => {});
    const endpoint = new V2Endpoint({ epoch: "pre", registry: new CapabilityRegistry(), send, timeout });
    await expect(endpoint.request("call", {}, { cancellation })).rejects.toThrow("cancelled");
    expect(send).not.toHaveBeenCalled();
    expect(timeout).not.toHaveBeenCalled();
    endpoint.close();
  });

  it("forwards cancellation through the client passed to a capability", async () => {
    const registry = new CapabilityRegistry();
    registry.register("outer", async (_method, _params, context) => {
      const cancellation = new V2RequestCancellation();
      const pending = context.client!.request("remote", { text: "x".repeat(2000) }, { cancellation });
      cancellation.cancel();
      try {
        await pending;
        return { cancelled: false };
      } catch (error) {
        return { cancelled: error instanceof Error && error.message === "request cancelled" };
      }
    });
    const frames: string[] = [];
    const endpoint = new V2Endpoint({
      epoch: "nested",
      registry,
      send: async (frame) => {
        frames.push(frame);
      },
      timeout: () => () => {},
    });
    const request = { v: "MCBEWS/2", kind: "request", id: "p.nested.1", method: "outer", params: {} };
    for (const frame of fragment(request, "p.nested.2", "down")) await endpoint.receive(frame, "Entity");
    await endpoint.drain();
    expect(frames[0]).toContain('"method":"remote"');
    const nestedTransport = frames[0].split("|")[1];
    expect(frames.filter((frame) => frame.split("|")[1] === nestedTransport)).toHaveLength(1);
    const pool = new ReassemblyPool();
    const response = frames.map((frame) => pool.receive("c", "s", frame, "up")).find((value) => value !== null);
    expect(response?.result).toEqual({ cancelled: true });
    endpoint.close();
  });

  it.each([
    { response: { result: "first" }, expected: "first" },
    { response: { error: { code: "HANDLER_FAILED", message: "remote failure" } }, expected: "remote failure" },
  ])("keeps an early response when cancellation, timeout, and send failure follow", async ({ response, expected }) => {
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    const frames: string[] = [];
    let timeoutCallback!: () => void;
    const cancelTimeout = vi.fn();
    const endpoint = new V2Endpoint({
      epoch: "early",
      registry: new CapabilityRegistry(),
      send: async (frame) => {
        frames.push(frame);
        await blocked;
        throw new Error("late send failure");
      },
      timeout: (callback) => {
        timeoutCallback = callback;
        return cancelTimeout;
      },
    });
    const cancellation = new V2RequestCancellation();
    const listener = vi.fn();
    const onCancel = cancellation.onCancel.bind(cancellation);
    vi.spyOn(cancellation, "onCancel").mockImplementation((callback) =>
      onCancel(() => {
        listener();
        callback();
      })
    );
    const pending = endpoint.request("large", { text: "x".repeat(2000) }, { cancellation });
    const result = response.error
      ? expect(pending).rejects.toThrow(expected)
      : expect(pending).resolves.toMatchObject({ result: expected });
    const id = /"id":"([^"]+)"/.exec(frames[0])?.[1];
    expect(id).toBeDefined();
    const reply = { v: "MCBEWS/2", kind: "response", id, ...response };
    const receiving = fragment(reply, "p.early.1", "down").map((frame) => endpoint.receive(frame, "Entity"));
    cancellation.cancel();
    timeoutCallback();
    endpoint.close();
    release();
    await Promise.all(receiving);
    await result;
    await Promise.resolve();
    await Promise.resolve();
    expect(frames).toHaveLength(1);
    expect(cancelTimeout).toHaveBeenCalledOnce();
    expect(listener).not.toHaveBeenCalled();
  });

  it("keeps timeout as the first outcome and detaches cancellation", async () => {
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    const frames: string[] = [];
    let timeoutCallback!: () => void;
    const cancelTimeout = vi.fn();
    const endpoint = new V2Endpoint({
      epoch: "timeout",
      registry: new CapabilityRegistry(),
      send: async (frame) => {
        frames.push(frame);
        await blocked;
      },
      timeout: (callback) => {
        timeoutCallback = callback;
        return cancelTimeout;
      },
    });
    const cancellation = new V2RequestCancellation();
    const listener = vi.fn();
    const onCancel = cancellation.onCancel.bind(cancellation);
    vi.spyOn(cancellation, "onCancel").mockImplementation((callback) =>
      onCancel(() => {
        listener();
        callback();
      })
    );
    const pending = endpoint.request("large", { text: "x".repeat(2000) }, { cancellation });
    const result = expect(pending).rejects.toThrow("timed out");
    const id = /"id":"([^"]+)"/.exec(frames[0])?.[1];
    timeoutCallback();
    const reply = { v: "MCBEWS/2", kind: "response", id, result: "late" };
    for (const frame of fragment(reply, "p.timeout.1", "down")) await endpoint.receive(frame, "Entity");
    cancellation.cancel();
    endpoint.close();
    release();
    await result;
    await Promise.resolve();
    await Promise.resolve();
    expect(frames).toHaveLength(1);
    expect(cancelTimeout).toHaveBeenCalledOnce();
    expect(listener).not.toHaveBeenCalled();
  });

  it("cleans a failed send and permits another request", async () => {
    const frames: string[] = [];
    const cancelTimeout = vi.fn();
    let fail = true;
    const endpoint = new V2Endpoint({
      epoch: "failure",
      registry: new CapabilityRegistry(),
      maxPending: 1,
      send: async (frame) => {
        if (fail) throw new Error("send failed");
        frames.push(frame);
      },
      timeout: () => cancelTimeout,
    });
    const cancellation = new V2RequestCancellation();
    const listener = vi.fn();
    const onCancel = cancellation.onCancel.bind(cancellation);
    vi.spyOn(cancellation, "onCancel").mockImplementation((callback) =>
      onCancel(() => {
        listener();
        callback();
      })
    );
    await expect(endpoint.request("call", {}, { cancellation })).rejects.toThrow("send failed");
    cancellation.cancel();
    expect(listener).not.toHaveBeenCalled();
    expect(cancelTimeout).toHaveBeenCalledOnce();
    fail = false;
    const pending = endpoint.request("next", {});
    const request = new ReassemblyPool().receive("c", "s", frames[0], "up");
    const reply = { v: "MCBEWS/2", kind: "response", id: request!.id, result: 42 };
    for (const frame of fragment(reply, "p.failure.1", "down")) await endpoint.receive(frame, "Entity");
    expect((await pending).result).toBe(42);
    expect(cancelTimeout).toHaveBeenCalledTimes(2);
    endpoint.close();
  });

  it("handles a timer firing synchronously during registration", async () => {
    const send = vi.fn(async (_frame: string) => {});
    const cancelTimeout = vi.fn();
    const endpoint = new V2Endpoint({
      epoch: "immediate",
      registry: new CapabilityRegistry(),
      send,
      timeout: (callback) => {
        callback();
        return cancelTimeout;
      },
    });
    const cancellation = new V2RequestCancellation();
    const listener = vi.fn();
    const onCancel = cancellation.onCancel.bind(cancellation);
    vi.spyOn(cancellation, "onCancel").mockImplementation((callback) =>
      onCancel(() => {
        listener();
        callback();
      })
    );
    await expect(endpoint.request("call", {}, { cancellation })).rejects.toThrow("timed out");
    cancellation.cancel();
    expect(cancelTimeout).toHaveBeenCalledOnce();
    expect(listener).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    endpoint.close();
  });

  it("detaches all request listeners and stops sends on close", async () => {
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    const frames: string[] = [];
    const cancelTimeout = vi.fn();
    const endpoint = new V2Endpoint({
      epoch: "close",
      registry: new CapabilityRegistry(),
      send: async (frame) => {
        frames.push(frame);
        await blocked;
      },
      timeout: () => cancelTimeout,
    });
    const cancellations = [new V2RequestCancellation(), new V2RequestCancellation()];
    const listeners = cancellations.map((cancellation) => {
      const listener = vi.fn();
      const onCancel = cancellation.onCancel.bind(cancellation);
      vi.spyOn(cancellation, "onCancel").mockImplementation((callback) =>
        onCancel(() => {
          listener();
          callback();
        })
      );
      return listener;
    });
    const first = endpoint.request("one", { text: "x".repeat(2000) }, { cancellation: cancellations[0] });
    const second = endpoint.request("two", { text: "y".repeat(2000) }, { cancellation: cancellations[1] });
    const firstResult = expect(first).rejects.toThrow("closed");
    const secondResult = expect(second).rejects.toThrow("closed");
    endpoint.close();
    cancellations.forEach((cancellation) => cancellation.cancel());
    release();
    await Promise.all([firstResult, secondResult]);
    await Promise.resolve();
    await Promise.resolve();
    expect(frames).toHaveLength(2);
    expect(cancelTimeout).toHaveBeenCalledTimes(2);
    listeners.forEach((listener) => expect(listener).not.toHaveBeenCalled());
  });
});

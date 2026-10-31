import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilityRegistry, type CapabilityContext } from "../scripts/bridge/capabilityRegistry";
import { createDefaultCapabilityRegistry, defaultCapabilityRegistry } from "../scripts/bridge/capabilities";
import {
  _testingFlush,
  _testingReset,
  activateBridge,
  handleBridgeScriptEvent,
  registerBridgeRouter,
  registerCapability,
  setCapabilityHandler,
  unregisterCapability,
} from "../scripts/bridge/router";
import { BRIDGE_REQUEST_MESSAGE_ID } from "../scripts/bridge/constants";

const context: CapabilityContext = { caller: { kind: "server" }, requestVersion: 1 };

function event(capability: string, payload: Record<string, unknown> = {}, requestId = "r1") {
  return {
    id: BRIDGE_REQUEST_MESSAGE_ID,
    message: JSON.stringify({ request_id: requestId, capability, payload }),
    sourceType: "Server" as const,
  };
}

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  _testingReset();
});

describe("CapabilityRegistry", () => {
  it("composes a custom capability with the registered default", async () => {
    const registry = new CapabilityRegistry();
    registry.register("builtin", (_name, _payload) => ({ ok: true, source: "builtin" }));
    registry.register("custom", (_name, payload) => ({ ok: true, value: payload.value }));

    await expect(registry.dispatch("builtin", {}, context)).resolves.toEqual({
      ok: true,
      source: "builtin",
    });
    await expect(registry.dispatch("custom", { value: 7 }, context)).resolves.toEqual({
      ok: true,
      value: 7,
    });
    expect(registry.names()).toEqual(["builtin", "custom"]);
  });

  it("rejects duplicates, replaces explicitly, and ignores stale handles", async () => {
    const registry = new CapabilityRegistry();
    const first = registry.register("demo", () => ({ ok: true, source: "first" }));

    expect(() => registry.register("demo", () => ({ ok: true, source: "duplicate" }))).toThrow(/already registered/);

    const second = registry.register("demo", () => ({ ok: true, source: "second" }), {
      replace: true,
    });
    expect(registry.unregister(first)).toBe(false);
    await expect(registry.dispatch("demo", {}, context)).resolves.toEqual({
      ok: true,
      source: "second",
    });
    expect(registry.unregister(second)).toBe(true);
    expect(registry.has("demo")).toBe(false);
  });

  it("keeps an in-flight call after unregister and hides the name afterwards", async () => {
    const registry = new CapabilityRegistry();
    const started = deferred();
    const gate = deferred();

    const registration = registry.register("slow", async () => {
      started.resolve();
      await gate.promise;
      return { ok: true, finished: true };
    });

    const pending = registry.dispatch("slow", {}, context);
    await started.promise;
    expect(registry.unregister(registration)).toBe(true);
    gate.resolve();

    await expect(pending).resolves.toEqual({ ok: true, finished: true });
    await expect(registry.dispatch("slow", {}, context)).resolves.toEqual({
      ok: false,
      error: { code: "UNSUPPORTED_CAPABILITY", message: "unsupported capability: slow" },
    });
  });

  it("returns structured results for unknown, invalid, and failing entries", async () => {
    const registry = new CapabilityRegistry();
    registry.register("validated", (_name, payload) => ({ ok: true, count: payload.count }), {
      validate: (payload) => (typeof payload.count === "number" ? null : "count must be a number"),
    });
    registry.register("failing", () => {
      throw new Error("boom");
    });
    registry.register("healthy", () => ({ ok: true, alive: true }));

    await expect(registry.dispatch("missing", {}, context)).resolves.toEqual({
      ok: false,
      error: { code: "UNSUPPORTED_CAPABILITY", message: "unsupported capability: missing" },
    });
    await expect(registry.dispatch("validated", { count: "nope" }, context)).resolves.toEqual({
      ok: false,
      error: { code: "INVALID_REQUEST", message: "count must be a number" },
    });
    await expect(registry.dispatch("failing", {}, context)).resolves.toEqual({
      ok: false,
      error: { code: "CAPABILITY_FAILED", message: "capability handler failed" },
    });
    await expect(registry.dispatch("healthy", {}, context)).resolves.toEqual({
      ok: true,
      alive: true,
    });
    await expect(registry.dispatch("validated", { count: 2 }, context)).resolves.toEqual({
      ok: true,
      count: 2,
    });
  });
});

describe("router capability registration", () => {
  it("routes a custom capability without changing the router and keeps built-ins working", async () => {
    const sender = vi.fn(async (_requestId: string, _body: string) => {});
    await activateBridge(sender);
    registerCapability("echo", (_name, payload) => ({ ok: true, echo: payload.text }));

    await handleBridgeScriptEvent(event("echo", { text: "hi" }, "r1"));
    await handleBridgeScriptEvent(event("get_player_snapshot", {}, "r2"));

    expect(JSON.parse(sender.mock.calls[0][1])).toEqual({ ok: true, echo: "hi" });
    expect(JSON.parse(sender.mock.calls[1][1])).toEqual({
      ok: true,
      payload: { players: [] },
    });
  });

  it("returns INVALID_REQUEST for a rejected payload", async () => {
    const sender = vi.fn(async (_requestId: string, _body: string) => {});
    await activateBridge(sender);

    await handleBridgeScriptEvent(event("get_player_snapshot", { target: 42 }, "r1"));

    expect(JSON.parse(sender.mock.calls[0][1])).toEqual({
      ok: false,
      error: { code: "INVALID_REQUEST", message: "payload.target must be a string" },
    });
  });

  it("keeps the legacy aggregate handler override semantics", async () => {
    const sender = vi.fn(async (_requestId: string, _body: string) => {});
    await activateBridge(sender);
    const aggregate = vi.fn(async () => ({ ok: true, handledBy: "aggregate" }));
    setCapabilityHandler(aggregate);

    await handleBridgeScriptEvent(event("get_player_snapshot", {}, "r1"));
    registerCapability("custom", () => ({ ok: true, handledBy: "registry" }));
    await handleBridgeScriptEvent(event("custom", {}, "r2"));

    expect(aggregate).toHaveBeenCalledTimes(2);
    expect(JSON.parse(sender.mock.calls[0][1])).toEqual({ ok: true, handledBy: "aggregate" });
    expect(JSON.parse(sender.mock.calls[1][1])).toEqual({ ok: true, handledBy: "aggregate" });

    _testingReset();
    await activateBridge(sender);
    await handleBridgeScriptEvent(event("get_player_snapshot", {}, "r3"));
    expect(JSON.parse(sender.mock.calls[2][1])).toEqual({ ok: true, payload: { players: [] } });
  });

  it("prevents a stale handle from removing a replacement registration", async () => {
    const first = registerCapability("demo", () => ({ ok: true, source: "first" }));
    const second = registerCapability("demo", () => ({ ok: true, source: "second" }), {
      replace: true,
    });

    expect(unregisterCapability(first)).toBe(false);
    expect(unregisterCapability(second)).toBe(true);
  });
});

describe("default table registration", () => {
  afterEach(() => {
    delete defaultCapabilityRegistry.regression_table_entry;
    _testingReset();
  });

  it("registers every entry exported by the default table", async () => {
    defaultCapabilityRegistry.regression_table_entry = () => ({ ok: true, source: "table" });

    const registry = createDefaultCapabilityRegistry();
    expect(registry.has("regression_table_entry")).toBe(true);
    await expect(registry.dispatch("regression_table_entry", {}, context)).resolves.toEqual({
      ok: true,
      source: "table",
    });

    _testingReset();
    const sender = vi.fn(async (_requestId: string, _body: string) => {});
    await activateBridge(sender);
    await handleBridgeScriptEvent(event("regression_table_entry", {}, "r1"));

    expect(JSON.parse(sender.mock.calls[0][1])).toEqual({ ok: true, source: "table" });
  });

  it("looks up existing default entries in the exported table at dispatch time", async () => {
    const registry = createDefaultCapabilityRegistry();
    const original = defaultCapabilityRegistry.get_player_snapshot;
    defaultCapabilityRegistry.get_player_snapshot = () => ({ ok: true, source: "replaced" });
    try {
      await expect(registry.dispatch("get_player_snapshot", {}, context)).resolves.toEqual({
        ok: true,
        source: "replaced",
      });
    } finally {
      defaultCapabilityRegistry.get_player_snapshot = original;
    }
  });
});

describe("router queue order with registered handlers", () => {
  it("runs registered custom handlers serially in FIFO request order", async () => {
    const sender = vi.fn(async (_requestId: string, _body: string) => {});
    await activateBridge(sender);
    const finished: string[] = [];
    registerCapability("ordered", async (_name, payload) => {
      await Promise.resolve();
      finished.push(String(payload.requestId));
      return { ok: true };
    });
    registerBridgeRouter();

    for (const requestId of ["r1", "r2", "r3"]) {
      system.afterEvents.scriptEventReceive.emit({
        id: BRIDGE_REQUEST_MESSAGE_ID,
        message: JSON.stringify({ request_id: requestId, capability: "ordered", payload: { requestId } }),
        sourceType: "Server",
      } as ScriptEventCommandMessageAfterEvent);
    }
    await _testingFlush();

    expect(finished).toEqual(["r1", "r2", "r3"]);
    expect(sender.mock.calls.map((call) => call[0])).toEqual(["r1", "r2", "r3"]);
  });
});

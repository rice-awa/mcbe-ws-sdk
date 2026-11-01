import { beforeEach, describe, expect, it, vi } from "vitest";
import { system, type ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import {
  activateBridge,
  enqueueOrHandle,
  getV2Endpoint,
  registerBridgeRouter,
  registerCapability,
  unregisterCapability,
  _testingFlush,
  _testingGetPeerCount,
  _testingGetPendingHello,
  _testingReset,
} from "../scripts/bridge/router";
import { defaultCapabilityRegistry } from "../scripts/bridge/capabilities";
import { MANIFEST } from "../scripts/bridge/v2/assets";
import { Message } from "../scripts/bridge/v2/codec";
import { fragment, ReassemblyPool } from "../scripts/bridge/v2/transport";

const NEGOTIATE = MANIFEST.negotiation.event;
const PACKET = MANIFEST.carrier.event;

function event(id: string, message: string, sourceType = "Server"): ScriptEventCommandMessageAfterEvent {
  return { id, message, sourceType } as ScriptEventCommandMessageAfterEvent;
}

let sent: string[];
let sequence = 0;

function activate(): Promise<void> {
  sent = [];
  return activateBridge(async (frame) => {
    sent.push(frame);
  });
}

function hello(attempt: string): void {
  enqueueOrHandle(event(NEGOTIATE, `MCBEWS2H|${attempt}`));
}

function challenge(attempt: string): string {
  hello(attempt);
  const parts = sent[sent.length - 1].split("|");
  expect(parts.slice(0, 2)).toEqual(["MCBEWS2N", attempt]);
  expect(parts.slice(3)).toEqual(["2", MANIFEST.negotiation.features.join(",")]);
  return parts[2];
}

function confirm(attempt: string, epoch: string): void {
  enqueueOrHandle(event(NEGOTIATE, `MCBEWS2C|${attempt}|${epoch}`));
  expect(sent[sent.length - 1]).toBe(`MCBEWS2N|${attempt}|${epoch}|ready`);
}

function admit(attempt: string): string {
  const epoch = challenge(attempt);
  confirm(attempt, epoch);
  return epoch;
}

async function sendRequest(epoch: string, method: string, id: string, params: unknown = {}): Promise<void> {
  const message = { v: "MCBEWS/2", kind: "request", id, method, params };
  for (const frame of fragment(message, `p.${epoch}.${++sequence}`, "down")) {
    enqueueOrHandle(event(PACKET, frame));
  }
  await _testingFlush();
}

async function sendNotification(epoch: string, method: string, params: unknown = {}): Promise<void> {
  const message = { v: "MCBEWS/2", kind: "notification", method, params };
  for (const frame of fragment(message, `p.${epoch}.${++sequence}`, "down")) {
    enqueueOrHandle(event(PACKET, frame));
  }
  await _testingFlush();
}

function carrierResponses(): Message[] {
  const pool = new ReassemblyPool();
  return sent
    .filter((frame) => frame.startsWith("MCBEWS2|"))
    .map((frame) => pool.receive("c", "s", frame, "up"))
    .filter((message): message is Message => message !== null);
}

beforeEach(() => {
  _testingReset();
  system.afterEvents.scriptEventReceive.clear();
  sequence = 0;
});

describe("bridge router activation", () => {
  it("subscribes once and retains a single sanitized hello before activation", async () => {
    registerBridgeRouter();
    registerBridgeRouter();

    hello("first");
    hello("second");
    hello("invalid|extra");
    enqueueOrHandle(event(NEGOTIATE, `MCBEWS2H|${"x".repeat(120)}`));

    expect(_testingGetPendingHello()).toBe("second");
    await activate();

    const parts = sent[0].split("|");
    expect(parts.slice(0, 2)).toEqual(["MCBEWS2N", "second"]);
    expect(getV2Endpoint(parts[2])).toBeNull();
    expect(_testingGetPendingHello()).toBeNull();

    confirm("second", parts[2]);
    expect(getV2Endpoint(parts[2])?.epoch).toBe(parts[2]);
    expect(_testingGetPeerCount()).toBe(1);
  });

  it("does not dispatch a pre-activation hello as business", async () => {
    const handler = vi.fn(async () => ({ ok: true }));
    registerCapability("probe", handler);

    hello("early");
    expect(_testingGetPendingHello()).toBe("early");
    expect(handler).not.toHaveBeenCalled();
  });

  it("drops business frames received before activation without replaying them", async () => {
    const handler = vi.fn(async () => ({ ok: true }));
    registerCapability("probe", handler);
    const epoch = "pending";

    await sendNotification(epoch, "probe");
    expect(handler).not.toHaveBeenCalled();

    await activate();
    await _testingFlush();
    expect(handler).not.toHaveBeenCalled();
    expect(sent).toEqual([]);
  });

  it("reaches ready after one challenge confirmation and routes requests", async () => {
    await activate();
    const handler = vi.fn(async (_name: string, payload: Record<string, unknown>) => ({ echo: payload }));
    registerCapability("echo", handler);

    const epoch = admit("session");
    await sendRequest(epoch, "echo", `p.${epoch}.1`, { value: 7 });

    expect(handler).toHaveBeenCalledWith("echo", { value: 7 }, expect.objectContaining({ requestVersion: 2 }));
    expect(carrierResponses()).toEqual([
      {
        v: "MCBEWS/2",
        kind: "response",
        id: `p.${epoch}.1`,
        result: { echo: { value: 7 } },
        meta: {},
      },
    ]);
  });

  it("routes notifications and refreshes an admitted lease", async () => {
    await activate();
    const handler = vi.fn(async () => ({}));
    registerCapability("notice", handler);
    const epoch = admit("lease");

    for (const _ of [0, 1]) await sendNotification(epoch, "notice", { text: "x" });
    expect(handler).toHaveBeenCalledTimes(2);

    enqueueOrHandle(event(NEGOTIATE, `MCBEWS2R|${epoch}`));
    expect(sent[sent.length - 1]).toBe(`MCBEWS2N|${epoch}|lease`);

    enqueueOrHandle(event(NEGOTIATE, `MCBEWS2X|${epoch}`));
    expect(getV2Endpoint(epoch)).toBeNull();
  });

  it("returns null for an unknown epoch", () => {
    expect(getV2Endpoint("missing")).toBeNull();
  });
});

describe("retired MCBEWS/1 surface", () => {
  const retired = ["mcbews:bridge_req", "mcbews:session_req", "mcbews:text_resp"];
  const legacyBody = JSON.stringify({
    v: 1,
    request_id: "old-1",
    capability: "probe",
    payload: {},
    action: "list",
  });

  it("logs a structured rejection with zero capability calls or responses", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const handler = vi.fn(async () => ({ ok: true }));
    registerCapability("probe", handler);
    await activate();

    for (const id of retired) {
      enqueueOrHandle(event(id, legacyBody, "Server"));
      enqueueOrHandle(event(id, legacyBody, "Entity"));
    }

    expect(handler).not.toHaveBeenCalled();
    expect(sent).toEqual([]);
    for (const id of retired) {
      expect(warn).toHaveBeenCalledWith(expect.stringContaining(`event=${id}`));
    }
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("expected=MCBEWS/2"));
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("reason=UNSUPPORTED_PROTOCOL"));
    warn.mockRestore();
  });

  it("reports a server admission rejection with its reason", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await activate();

    enqueueOrHandle(event(MANIFEST.admission.reject_notice, "NEGOTIATION_TIMEOUT"));

    expect(warn).toHaveBeenCalledWith(expect.stringContaining("reason=NEGOTIATION_TIMEOUT"));
    expect(warn).not.toHaveBeenCalledWith(expect.stringContaining("reason=UNSUPPORTED_PROTOCOL"));
    expect(sent).toEqual([]);
    warn.mockRestore();
  });

  it("logs any unknown event in the bridge namespace and ignores other namespaces", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const handler = vi.fn(async () => ({ ok: true }));
    registerCapability("probe", handler);
    await activate();

    enqueueOrHandle(event("mcbews:future_event", legacyBody));
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("event=mcbews:future_event"));

    warn.mockClear();
    enqueueOrHandle(event("other_addon:event", legacyBody));
    expect(warn).not.toHaveBeenCalled();
    expect(handler).not.toHaveBeenCalled();
    expect(sent).toEqual([]);
    warn.mockRestore();
  });

  it("never queues a retired request for a later activation", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const handler = vi.fn(async () => ({ ok: true }));
    registerCapability("probe", handler);

    enqueueOrHandle(event("mcbews:bridge_req", legacyBody));
    expect(_testingGetPendingHello()).toBeNull();

    await activate();
    await _testingFlush();
    expect(handler).not.toHaveBeenCalled();
    expect(sent).toEqual([]);
    warn.mockRestore();
  });
});

describe("capability registration", () => {
  it("registers and unregisters a custom capability", async () => {
    await activate();
    const epoch = admit("registry");
    const registration = registerCapability("custom", () => ({ ok: true, source: "custom" }));

    await sendRequest(epoch, "custom", `p.${epoch}.1`);
    expect(carrierResponses()[0].result).toEqual({ ok: true, source: "custom" });

    expect(unregisterCapability(registration)).toBe(true);
    sent = [];
    await sendRequest(epoch, "custom", `p.${epoch}.2`);
    expect(carrierResponses()[0].error).toEqual({
      code: "UNSUPPORTED_METHOD",
      message: "unsupported capability: custom",
    });
  });

  it("keeps the two read-only defaults and no world command", () => {
    expect(Object.keys(defaultCapabilityRegistry).sort()).toEqual(["get_inventory_snapshot", "get_player_snapshot"]);
    expect(defaultCapabilityRegistry).not.toHaveProperty("run_world_command");
  });
});

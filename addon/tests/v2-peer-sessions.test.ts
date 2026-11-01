import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import {
  activateBridge,
  enqueueOrHandle,
  getV2Endpoint,
  registerCapability,
  V2_MAX_PEERS,
  V2_MAX_CHALLENGES,
  _testingReset,
} from "../scripts/bridge/router";
import { fragment } from "../scripts/bridge/v2/transport";

const event = (id: string, message: string): ScriptEventCommandMessageAfterEvent =>
  ({ id, message, sourceType: "Entity" }) as ScriptEventCommandMessageAfterEvent;
const control = (message: string) => enqueueOrHandle(event("mcbews:negotiate_v2", message));
const packet = (message: string) => enqueueOrHandle(event("mcbews:packet_v2", message));
let sent: string[];

function challenge(attempt: string): string {
  control(`MCBEWS2H|${attempt}`);
  const parts = sent[sent.length - 1].split("|");
  expect(parts.slice(0, 2)).toEqual(["MCBEWS2N", attempt]);
  expect(parts[3]).toBe("2");
  return parts[2];
}
function confirm(attempt: string, epoch: string): void {
  control(`MCBEWS2C|${attempt}|${epoch}`);
  expect(sent[sent.length - 1]).toBe(`MCBEWS2N|${attempt}|${epoch}|ready`);
}
function admit(attempt: string): string {
  const epoch = challenge(attempt);
  confirm(attempt, epoch);
  return epoch;
}

beforeEach(async () => {
  _testingReset();
  vi.useFakeTimers();
  sent = [];
  await activateBridge(async (frame) => {
    sent.push(frame);
  });
});
afterEach(() => {
  _testingReset();
  vi.useRealTimers();
});

describe("independent v2 sessions", () => {
  it("keeps a peer's incomplete message and pending reverse call while another joins", async () => {
    const handler = vi.fn(async () => ({}));
    registerCapability("notice", handler);
    const first = admit("first");
    const endpoint = getV2Endpoint(first)!;
    const pending = endpoint.request("remote", {});
    const notice = { v: "MCBEWS/2", kind: "notification", method: "notice", params: { text: "x".repeat(2000) } };
    const frames = fragment(notice, `p.${first}.9`, "down");
    packet(frames[0]);
    const second = admit("second");
    expect(getV2Endpoint(first)).toBe(endpoint);
    expect(getV2Endpoint(second)).not.toBeNull();
    frames.slice(1).forEach(packet);
    await endpoint.drain();
    expect(handler).toHaveBeenCalledTimes(1);
    control(`MCBEWS2X|${first}`);
    await expect(pending).rejects.toThrow("closed");
    expect(getV2Endpoint(second)).not.toBeNull();
  });

  it("cannot reactivate a retired epoch with delayed hello or confirmation", () => {
    const first = admit("attempt");
    control(`MCBEWS2X|${first}`);
    const next = challenge("attempt");
    expect(next).not.toBe(first);
    control(`MCBEWS2C|attempt|${first}`);
    expect(getV2Endpoint(first)).toBeNull();
    expect(getV2Endpoint(next)).toBeNull();
    confirm("attempt", next);
    expect(getV2Endpoint(next)).not.toBeNull();
  });

  it("renews leases, expires abandoned sessions, and admits more than 2048 sequential peers", async () => {
    const first = admit("first");
    vi.advanceTimersByTime(45000);
    control(`MCBEWS2R|${first}`);
    vi.advanceTimersByTime(45000);
    expect(getV2Endpoint(first)).not.toBeNull();
    vi.advanceTimersByTime(20000);
    expect(getV2Endpoint(first)).toBeNull();
    for (let i = 0; i < 2050; i++) {
      const epoch = admit(`n${i}`);
      control(`MCBEWS2X|${epoch}`);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    }
    expect(getV2Endpoint(admit("after"))).not.toBeNull();
  });
  it("returns busy at peer and provisional limits without displacing live peers", async () => {
    const peers: string[] = [];
    for (let i = 0; i < V2_MAX_PEERS; i++) {
      peers.push(admit(`peer${i}`));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    }
    control("MCBEWS2H|overflow");
    expect(sent[sent.length - 1]).toBe("MCBEWS2N|overflow|-|busy");
    expect(peers.every((epoch) => getV2Endpoint(epoch))).toBe(true);
    peers.forEach((epoch) => control(`MCBEWS2X|${epoch}`));
    for (let i = 0; i < V2_MAX_CHALLENGES; i++) {
      challenge(`provisional${i}`);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    }
    control("MCBEWS2H|overflow2");
    expect(sent[sent.length - 1]).toBe("MCBEWS2N|overflow2|-|busy");
    vi.advanceTimersByTime(10001);
    expect(challenge("new")).toBeTruthy();
  });

  it("gates endpoint features before sending", async () => {
    const { V2Endpoint } = await import("../scripts/bridge/v2/runtime");
    const { CapabilityRegistry } = await import("../scripts/bridge/capabilityRegistry");
    const sender = vi.fn(async () => {});
    const endpoint = new V2Endpoint({
      epoch: "empty",
      registry: new CapabilityRegistry(),
      send: sender,
      timeout: () => () => {},
      features: [],
    });
    await expect(endpoint.request("any", {})).rejects.toThrow("reverse/1");
    await expect(endpoint.notify("any", {}, { metadata: { value: 1 } })).rejects.toThrow("metadata/1");
    await expect(endpoint.notify("any", {}, { require: ["stream/1"] })).rejects.toThrow("stream/1");
    expect(sender).not.toHaveBeenCalled();
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";
import { system, type ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import {
  activateBridge,
  enqueueOrHandle,
  getV2Endpoint,
  registerCapability,
  _testingFlush,
  _testingReset,
} from "../scripts/bridge/router";
import { fragment } from "../scripts/bridge/v2/transport";
import { MANIFEST } from "../scripts/bridge/v2/assets";

beforeEach(() => {
  _testingReset();
  system.afterEvents.scriptEventReceive.clear();
});

function event(id: string, message: string): ScriptEventCommandMessageAfterEvent {
  return { id, message, sourceType: "Entity" } as ScriptEventCommandMessageAfterEvent;
}

async function activate(sent: string[]): Promise<void> {
  await activateBridge(async (frame) => {
    sent.push(frame);
  });
}

describe("v2 discovery", () => {
  it("holds at most one valid hello while the bridge starts", async () => {
    const sent: string[] = [];
    enqueueOrHandle(event(MANIFEST.negotiation.event, "MCBEWS2H|first"));
    enqueueOrHandle(event(MANIFEST.negotiation.event, "MCBEWS2H|second"));
    enqueueOrHandle(event(MANIFEST.negotiation.event, "MCBEWS2H|invalid|extra"));
    await activate(sent);
    const parts = sent[0].split("|");
    expect(parts.slice(0, 2)).toEqual(["MCBEWS2N", "second"]);
    expect(parts.slice(3)).toEqual(["2", MANIFEST.negotiation.features.join(",")]);
    expect(getV2Endpoint(parts[2])).toBeNull();
    enqueueOrHandle(event(MANIFEST.negotiation.event, `MCBEWS2C|second|${parts[2]}`));
    expect(getV2Endpoint(parts[2])?.epoch).toBe(parts[2]);
  });

  it("does not dispatch business before a fresh challenge is confirmed", async () => {
    const sent: string[] = [];
    const handler = vi.fn(async () => ({ ok: true }));
    registerCapability("test", handler);
    await activate(sent);
    enqueueOrHandle(event(MANIFEST.negotiation.event, "MCBEWS2H|first"));
    const epoch = sent[0].split("|")[2];
    enqueueOrHandle(event(MANIFEST.negotiation.event, `MCBEWS2C|first|${epoch}`));
    const endpoint = getV2Endpoint(epoch)!;
    enqueueOrHandle(event(MANIFEST.negotiation.event, `MCBEWS2X|${epoch}`));
    enqueueOrHandle(event(MANIFEST.negotiation.event, "MCBEWS2H|first"));
    const next = sent[sent.length - 1].split("|")[2];
    expect(next).not.toBe(epoch);
    enqueueOrHandle(event(MANIFEST.negotiation.event, `MCBEWS2C|first|${epoch}`));
    const request = { v: "MCBEWS/2", kind: "notification", method: "test", params: {} };
    for (const frame of fragment(request, `p.${epoch}.1`, "down")) {
      enqueueOrHandle(event(MANIFEST.carrier.event, frame));
    }
    await endpoint.drain();
    await _testingFlush();
    expect(handler).not.toHaveBeenCalled();
  });

  it("rejects a retired legacy request without parsing it", async () => {
    const sent: string[] = [];
    const handler = vi.fn(async () => ({ ok: true }));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    registerCapability("legacy_test", handler);
    await activate(sent);

    enqueueOrHandle(
      event("mcbews:bridge_req", JSON.stringify({ request_id: "old", capability: "legacy_test", payload: {} }))
    );
    await _testingFlush();

    expect(handler).not.toHaveBeenCalled();
    expect(sent).toEqual([]);
    expect(getV2Endpoint("old")).toBeNull();
    warn.mockRestore();
  });
});

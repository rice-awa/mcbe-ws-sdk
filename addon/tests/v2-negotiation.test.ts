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

beforeEach(() => {
  _testingReset();
  system.afterEvents.scriptEventReceive.clear();
});

function event(id: string, message: string): ScriptEventCommandMessageAfterEvent {
  return { id, message, sourceType: "Entity" } as ScriptEventCommandMessageAfterEvent;
}

describe("v2 discovery", () => {
  it("continues to serve old clients without an initial hello", async () => {
    const sendV1 = vi.fn(async () => {});
    registerCapability("legacy_test", async () => ({ ok: true }));
    await activateBridge(sendV1, async () => {});
    enqueueOrHandle(
      event("mcbews:bridge_req", JSON.stringify({ request_id: "old", capability: "legacy_test", payload: {} }))
    );
    await _testingFlush();
    expect(sendV1).toHaveBeenCalledWith("old", '{"ok":true}');
    expect(getV2Endpoint()).toBeNull();
  });

  it("holds at most one valid hello while the bridge starts", async () => {
    const sent: string[] = [];
    enqueueOrHandle(event("mcbews:negotiate_v2", "MCBEWS2H|first"));
    enqueueOrHandle(event("mcbews:negotiate_v2", "MCBEWS2H|second"));
    enqueueOrHandle(event("mcbews:negotiate_v2", "MCBEWS2H|invalid|extra"));
    await activateBridge(
      async () => {},
      async (frame) => {
        sent.push(frame);
      }
    );
    expect(sent).toEqual(["MCBEWS2N|second|2|metadata/1,notifications/1,reverse/1"]);
    expect(getV2Endpoint()?.epoch).toBe("second");
  });

  it("selects v2 and rejects old epochs and replayed hellos", async () => {
    const sent: string[] = [];
    const handler = vi.fn(async () => ({ ok: true }));
    registerCapability("test", handler);
    await activateBridge(
      async () => {},
      async (frame) => {
        sent.push(frame);
      }
    );
    enqueueOrHandle(event("mcbews:negotiate_v2", "MCBEWS2H|first"));
    expect(sent).toContain("MCBEWS2N|first|2|metadata/1,notifications/1,reverse/1");
    enqueueOrHandle(event("mcbews:negotiate_v2", "MCBEWS2H|second"));
    enqueueOrHandle(event("mcbews:negotiate_v2", "MCBEWS2H|first"));
    expect(getV2Endpoint()?.epoch).toBe("second");
    const request = { v: "MCBEWS/2", kind: "notification", method: "test", params: {} };
    for (const frame of fragment(request, "p.first.1", "down")) {
      enqueueOrHandle(event("mcbews:packet_v2", frame));
    }
    for (const frame of fragment(request, "p.second.1", "down")) {
      enqueueOrHandle(event("mcbews:packet_v2", frame));
    }
    await getV2Endpoint()?.drain();
    expect(handler).toHaveBeenCalledTimes(1);
  });
});

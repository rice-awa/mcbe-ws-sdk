import { describe, expect, it, vi, beforeEach } from "vitest";
import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  activateBridge,
  handleBridgeScriptEvent,
  registerBridgeRouter,
  parseBridgeRequest,
  setCapabilityHandler,
  shouldHandleScriptEvent,
  MAX_PRE_READY_REQUESTS,
  _testingGetQueueSize,
  _testingFlush,
  _testingReset,
  type BridgeRequest,
  type CapabilityHandler,
  type ResponseSender,
} from "../scripts/bridge/router";
import { defaultCapabilityRegistry } from "../scripts/bridge/capabilities";
import { BRIDGE_MESSAGE_ID } from "../scripts/bridge/constants";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const vectors = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../tests/fixtures/legacy_mcbeai_v1_vectors.json", import.meta.url)),
    "utf-8",
  ),
) as { bridge_requests: Array<{ name: string; version: number; message: string }> };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function serverEvent(message: string) {
  return {
    id: BRIDGE_MESSAGE_ID,
    message,
    sourceType: "Server" as const,
  };
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  _testingReset();
  system.afterEvents.scriptEventReceive.clear();
});

// ---------------------------------------------------------------------------
// shouldHandleScriptEvent
// ---------------------------------------------------------------------------

describe("shouldHandleScriptEvent", () => {
  it("returns true for bridge message ID", () => {
    expect(shouldHandleScriptEvent(BRIDGE_MESSAGE_ID)).toBe(true);
  });

  it("returns false for other message IDs", () => {
    expect(shouldHandleScriptEvent("other:id")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Source acceptance
// ---------------------------------------------------------------------------

describe("source acceptance", () => {
  it("accepts Entity source (WS /wsserver commandRequest path)", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    // Entity-originated events must still be processed: Bedrock tags
    // /wsserver-delivered scriptevents as Entity, not Server.
    await handleBridgeScriptEvent({
      id: BRIDGE_MESSAGE_ID,
      message: JSON.stringify({ request_id: "r1", capability: "missing", payload: {} }),
      sourceType: "Entity",
    });

    expect(sender).toHaveBeenCalledOnce();
    expect(sender).toHaveBeenCalledWith("r1", expect.stringContaining("UNSUPPORTED_CAPABILITY"));
  });

  it("processes Server source events", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    const handler = vi.fn<CapabilityHandler>().mockResolvedValue({ ok: true });
    setCapabilityHandler(handler);

    await handleBridgeScriptEvent(serverEvent(
      JSON.stringify({ request_id: "r2", capability: "test", payload: {} }),
    ));

    expect(handler).toHaveBeenCalledOnce();
    expect(sender).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// v1/v2 vectors
// ---------------------------------------------------------------------------

describe("v1 and v2 request vectors", () => {
  it("accepts shared v1 and v2 request vectors with trusted server caller", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    const handler = vi.fn<CapabilityHandler>().mockResolvedValue({ greeting: "hello" });
    setCapabilityHandler(handler);

    for (const vector of vectors.bridge_requests) {
      await handleBridgeScriptEvent(serverEvent(vector.message));
    }

    expect(handler).toHaveBeenNthCalledWith(
      1, "greet", { name: "Steve" }, { caller: { kind: "server" }, requestVersion: 1 },
    );
    expect(handler).toHaveBeenNthCalledWith(
      2, "greet", { name: "Steve" }, { caller: { kind: "server" }, requestVersion: 2 },
    );
  });
});

// ---------------------------------------------------------------------------
// Structured error responses
// ---------------------------------------------------------------------------

describe("structured error responses", () => {
  it("returns UNSUPPORTED_VERSION for a valid id", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    await handleBridgeScriptEvent(
      serverEvent(JSON.stringify({ v: 99, request_id: "r99", capability: "x", payload: {} })),
    );

    expect(sender).toHaveBeenCalledWith(
      "r99",
      JSON.stringify({
        ok: false,
        error: { code: "UNSUPPORTED_VERSION", message: "unsupported bridge version" },
      }),
    );
  });

  it("does not send error response for MALFORMED_JSON without requestId", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    await handleBridgeScriptEvent(serverEvent("not json at all"));

    // No requestId available from unparseable JSON — sender is not called
    expect(sender).not.toHaveBeenCalled();
  });

  it("does not send error response for non-record JSON without requestId", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    await handleBridgeScriptEvent(serverEvent("[1, 2, 3]"));

    // Array is valid JSON but not a record — no requestId possible
    expect(sender).not.toHaveBeenCalled();
  });

  it("returns UNSUPPORTED_CAPABILITY for unknown capability", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    await handleBridgeScriptEvent(
      serverEvent(JSON.stringify({ request_id: "r3", capability: "nonexistent", payload: {} })),
    );

    expect(sender).toHaveBeenCalledWith(
      "r3",
      JSON.stringify({
        ok: false,
        error: { code: "UNSUPPORTED_CAPABILITY", message: "unsupported capability: nonexistent" },
      }),
    );
  });

  it("returns CAPABILITY_FAILED when handler throws", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    const handler = vi.fn<CapabilityHandler>().mockRejectedValue(new Error("boom"));
    setCapabilityHandler(handler);

    await handleBridgeScriptEvent(
      serverEvent(JSON.stringify({ request_id: "r4", capability: "test", payload: {} })),
    );

    expect(sender).toHaveBeenCalledWith(
      "r4",
      JSON.stringify({
        ok: false,
        error: { code: "CAPABILITY_FAILED", message: "capability handler failed" },
      }),
    );
  });

  it("returns INVALID_REQUEST for missing capability field", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    await handleBridgeScriptEvent(
      serverEvent(JSON.stringify({ request_id: "r5", payload: {} })),
    );

    expect(sender).toHaveBeenCalledWith(
      "r5",
      JSON.stringify({
        ok: false,
        error: { code: "INVALID_REQUEST", message: "invalid bridge request" },
      }),
    );
  });

  it("stringifies error responses without stack traces or request payloads", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    const handler = vi.fn<CapabilityHandler>().mockRejectedValue(new Error("secret"));
    setCapabilityHandler(handler);

    await handleBridgeScriptEvent(
      serverEvent(JSON.stringify({ request_id: "r6", capability: "test", payload: { password: "hunter2" } })),
    );

    const sentBody = JSON.parse(sender.mock.calls[0][1]);
    expect(sentBody).toEqual({
      ok: false,
      error: { code: "CAPABILITY_FAILED", message: "capability handler failed" },
    });
    expect(JSON.stringify(sentBody)).not.toContain("secret");
    expect(JSON.stringify(sentBody)).not.toContain("hunter2");
    expect(JSON.stringify(sentBody)).not.toContain("stack");
    expect(JSON.stringify(sentBody)).not.toContain("Error");
  });
});

// ---------------------------------------------------------------------------
// Pre-ready queue
// ---------------------------------------------------------------------------

describe("pre-ready queue", () => {
  it("queues events before activation and drains after activate", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    registerBridgeRouter();

    system.afterEvents.scriptEventReceive.emit({
      id: BRIDGE_MESSAGE_ID,
      message: JSON.stringify({ request_id: "r1", capability: "get_player_snapshot", payload: {} }),
      sourceType: "Server",
    } as ScriptEventCommandMessageAfterEvent);

    expect(_testingGetQueueSize()).toBe(1);
    expect(sender).not.toHaveBeenCalled();

    await activateBridge(sender);
    expect(_testingGetQueueSize()).toBe(0);
    expect(sender).toHaveBeenCalledTimes(1);
  });

  it("preserves FIFO order", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    registerBridgeRouter();

    // Enqueue three events before activation
    for (let i = 1; i <= 3; i++) {
      system.afterEvents.scriptEventReceive.emit({
        id: BRIDGE_MESSAGE_ID,
        message: JSON.stringify({ request_id: `r${i}`, capability: "get_player_snapshot", payload: {} }),
        sourceType: "Server",
      } as ScriptEventCommandMessageAfterEvent);
    }

    await activateBridge(sender);
    expect(sender).toHaveBeenCalledTimes(3);
    // FIFO means r1 then r2 then r3
    expect(sender.mock.calls[0][0]).toBe("r1");
    expect(sender.mock.calls[1][0]).toBe("r2");
    expect(sender.mock.calls[2][0]).toBe("r3");
  });

  it("drops events when pre-ready queue exceeds max", () => {
    registerBridgeRouter();

    for (let i = 0; i < MAX_PRE_READY_REQUESTS; i++) {
      system.afterEvents.scriptEventReceive.emit({
        id: BRIDGE_MESSAGE_ID,
        message: JSON.stringify({ request_id: `r${i}`, capability: "x", payload: {} }),
        sourceType: "Server",
      } as ScriptEventCommandMessageAfterEvent);
    }

    expect(_testingGetQueueSize()).toBe(MAX_PRE_READY_REQUESTS);

    // One more should be silently dropped
    system.afterEvents.scriptEventReceive.emit({
      id: BRIDGE_MESSAGE_ID,
      message: JSON.stringify({ request_id: "overflow", capability: "x", payload: {} }),
      sourceType: "Server",
    } as ScriptEventCommandMessageAfterEvent);

    expect(_testingGetQueueSize()).toBe(MAX_PRE_READY_REQUESTS);
  });
});

// ---------------------------------------------------------------------------
// Response sender failures
// ---------------------------------------------------------------------------

describe("response sender failure handling", () => {
  it("catches response sender failures and does not reject", async () => {
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const sender: ResponseSender = vi.fn().mockRejectedValue(new Error("network down"));

    await activateBridge(sender);

    // This should not throw — the rejection is caught and logged
    await expect(
      handleBridgeScriptEvent(
        serverEvent(JSON.stringify({ request_id: "r99", capability: "get_player_snapshot", payload: {} })),
      ),
    ).resolves.toBeUndefined();

    expect(consoleSpy).toHaveBeenCalledWith(
      expect.stringContaining("r99"),
    );
    expect(consoleSpy).toHaveBeenCalledWith(
      expect.stringContaining("network down"),
    );
    consoleSpy.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// parseBridgeRequest unit tests
// ---------------------------------------------------------------------------

describe("parseBridgeRequest", () => {
  it("returns MALFORMED_JSON for unparseable input", () => {
    const result = parseBridgeRequest("{{{");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.error.code).toBe("MALFORMED_JSON");
      expect(result.requestId).toBeUndefined();
    }
  });

  it("returns valid BridgeRequest for v1 (missing v)", () => {
    const result = parseBridgeRequest(
      '{"request_id":"r1","capability":"greet","payload":{"name":"Steve"}}',
    );
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.request.v).toBe(1);
      expect(result.request.request_id).toBe("r1");
      expect(result.request.capability).toBe("greet");
      expect(result.request.payload).toEqual({ name: "Steve" });
    }
  });

  it("returns valid BridgeRequest for v2", () => {
    const result = parseBridgeRequest(
      '{"v":2,"request_id":"r2","capability":"greet","payload":{"x":1}}',
    );
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.request.v).toBe(2);
    }
  });

  it("rejects version 3 as UNSUPPORTED_VERSION", () => {
    const result = parseBridgeRequest(
      '{"v":3,"request_id":"r3","capability":"x","payload":{}}',
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.error.code).toBe("UNSUPPORTED_VERSION");
      expect(result.requestId).toBe("r3");
    }
  });

  it("rejects request_id as empty string", () => {
    const result = parseBridgeRequest(
      '{"v":1,"request_id":"","capability":"x","payload":{}}',
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.error.code).toBe("INVALID_REQUEST");
      expect(result.requestId).toBeUndefined();
    }
  });

  it("rejects missing capability", () => {
    const result = parseBridgeRequest(
      '{"v":1,"request_id":"r4","payload":{}}',
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.error.code).toBe("INVALID_REQUEST");
      expect(result.requestId).toBe("r4");
    }
  });

  it("accepts null payload as empty object", () => {
    const result = parseBridgeRequest(
      '{"v":2,"request_id":"r5","capability":"test"}',
    );
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.request.payload).toEqual({});
    }
  });

  it("rejects non-object payload", () => {
    const result = parseBridgeRequest(
      '{"v":1,"request_id":"r6","capability":"x","payload":"string"}',
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.error.code).toBe("INVALID_REQUEST");
    }
  });

  it("rejects array as root value", () => {
    const result = parseBridgeRequest('[1, 2, 3]');
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.error.code).toBe("INVALID_REQUEST");
      expect(result.requestId).toBeUndefined();
    }
  });
});

// ---------------------------------------------------------------------------
// defaultCapabilityRegistry check
// ---------------------------------------------------------------------------

describe("default capability registry", () => {
  it("does not expose run_world_command by default", () => {
    expect(defaultCapabilityRegistry).not.toHaveProperty("run_world_command");
  });

  it("exposes get_player_snapshot by default", () => {
    expect(defaultCapabilityRegistry).toHaveProperty("get_player_snapshot");
  });

  it("exposes get_inventory_snapshot by default", () => {
    expect(defaultCapabilityRegistry).toHaveProperty("get_inventory_snapshot");
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

describe("edge cases", () => {
  it("queues non-Server sourceType when bridge is not active yet", () => {
    registerBridgeRouter();

    // Entity events must be queued, not dropped — WS commandRequest arrives as Entity.
    system.afterEvents.scriptEventReceive.emit({
      id: BRIDGE_MESSAGE_ID,
      message: JSON.stringify({ request_id: "rx", capability: "x", payload: {} }),
      sourceType: "Entity",
    } as ScriptEventCommandMessageAfterEvent);

    expect(_testingGetQueueSize()).toBe(1);
  });

  it("handles empty message in handleBridgeScriptEvent gracefully", async () => {
    const sender = vi.fn(async (_rid: string, _body: string) => {});
    await activateBridge(sender);

    await handleBridgeScriptEvent(serverEvent(""));

    // No requestId from empty message — sender not called
    expect(sender).not.toHaveBeenCalled();
  });
});

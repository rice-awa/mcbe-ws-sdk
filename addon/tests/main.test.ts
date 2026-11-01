import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MANIFEST } from "../scripts/bridge/v2/assets";
import { fragment, ReassemblyPool } from "../scripts/bridge/v2/transport";

type RouterHarness = {
  _testingReset: () => void;
  _testingFlush: () => Promise<void>;
  _testingGetPendingHello: () => string | null;
  registerCapability: (name: string, handler: () => Record<string, unknown>) => unknown;
};
type MainHarness = Awaited<ReturnType<typeof loadMainHarness>>;
let harness: MainHarness;

async function loadMainHarness() {
  vi.resetModules();
  const minecraft = await import("@minecraft/server");
  const gametest = await import("@minecraft/server-gametest");
  minecraft.resetMinecraftMocks();
  const router = (await import("../scripts/bridge/router")) as unknown as RouterHarness;
  router._testingReset();
  await import("../scripts/main");
  const runCommand = vi.fn(() => ({ successCount: 1 }));
  const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  minecraft.world.getAllPlayers.mockReturnValue([
    {
      name: MANIFEST.carrier.trusted_player,
      runCommand,
      id: "tool1",
      location: { x: 0, y: 0, z: 0 },
      dimension: { id: "overworld" },
      getComponent: vi.fn(),
      getTags: vi.fn(() => []),
      getGameMode: vi.fn(),
    },
  ]);
  return { minecraft, gametest, router, runCommand, warnSpy };
}

function emittedFrames(runCommand: ReturnType<typeof vi.fn>): string[] {
  return runCommand.mock.calls
    .map((call) => String(call[0]))
    .filter((command) => command.startsWith("tell @s "))
    .map((command) => command.slice("tell @s ".length));
}

function controlFrame(frame: string, message: string): void {
  harness.minecraft.system.afterEvents.scriptEventReceive.emit({
    id: frame,
    sourceType: "Server",
    message,
  });
}

async function negotiate(attempt = "first"): Promise<string> {
  controlFrame(MANIFEST.negotiation.event, `MCBEWS2H|${attempt}`);
  const challenge = emittedFrames(harness.runCommand)
    .filter((frame) => frame.startsWith(`MCBEWS2N|${attempt}|`))
    .pop();
  expect(challenge).toBeDefined();
  const epoch = challenge!.split("|")[2];
  controlFrame(MANIFEST.negotiation.event, `MCBEWS2C|${attempt}|${epoch}`);
  expect(emittedFrames(harness.runCommand).pop()).toBe(`MCBEWS2N|${attempt}|${epoch}|ready`);
  return epoch;
}

function sendV2(epoch: string, message: Record<string, unknown>, tid: string): void {
  for (const frame of fragment(message, tid, "down")) {
    controlFrame(MANIFEST.carrier.event, frame);
  }
}

beforeEach(async () => {
  harness = await loadMainHarness();
});

afterEach(async () => {
  await harness.router._testingFlush();
  harness.router._testingReset();
  harness.minecraft.resetMinecraftMocks();
  harness.warnSpy.mockRestore();
});

describe("addon entry", () => {
  it("holds an early hello, then negotiates and answers a capability request", async () => {
    controlFrame(MANIFEST.negotiation.event, "MCBEWS2H|first");
    expect(harness.router._testingGetPendingHello()).toBe("first");
    expect(harness.runCommand).not.toHaveBeenCalled();

    harness.minecraft.world.afterEvents.worldLoad.emit({});
    const epoch = await negotiate();
    harness.router.registerCapability("main_probe", () => ({ wired: true }));

    sendV2(
      epoch,
      { v: "MCBEWS/2", kind: "request", id: `p.${epoch}.1`, method: "main_probe", params: {} },
      `p.${epoch}.1`
    );
    await harness.router._testingFlush();

    const pool = new ReassemblyPool();
    const response = emittedFrames(harness.runCommand)
      .filter((frame) => frame.startsWith("MCBEWS2|"))
      .map((frame) => pool.receive("c", "s", frame, "up"))
      .find((message) => message !== null);
    expect(response).toEqual({
      v: "MCBEWS/2",
      kind: "response",
      id: `p.${epoch}.1`,
      result: { wired: true },
      meta: {},
    });
  });

  it("drops a pre-activation business frame instead of buffering it", async () => {
    const epoch = "early";
    sendV2(epoch, { v: "MCBEWS/2", kind: "notification", method: "get_player_snapshot", params: {} }, `p.${epoch}.1`);
    expect(harness.router._testingGetPendingHello()).toBeNull();

    harness.minecraft.world.afterEvents.worldLoad.emit({});
    await harness.router._testingFlush();

    expect(emittedFrames(harness.runCommand).some((frame) => frame.startsWith("MCBEWS2|"))).toBe(false);
    expect(harness.runCommand).not.toHaveBeenCalled();
  });

  it("keeps the gate closed when tool initialization fails", async () => {
    harness.minecraft.world.getAllPlayers.mockReturnValue([]);
    harness.gametest.spawnSimulatedPlayer.mockImplementationOnce(() => {
      throw new Error("world not ready");
    });
    controlFrame(MANIFEST.negotiation.event, "MCBEWS2H|retry");
    harness.minecraft.world.afterEvents.worldLoad.emit({});
    await Promise.resolve();

    expect(harness.runCommand).not.toHaveBeenCalled();
    expect(harness.router._testingGetPendingHello()).toBe("retry");
  });
});

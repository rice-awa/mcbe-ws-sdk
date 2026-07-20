import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { BRIDGE_REQUEST_MESSAGE_ID } from "../scripts/bridge/constants";
import { MAX_PRE_READY_REQUESTS } from "../scripts/bridge/router";

type MainHarness = Awaited<ReturnType<typeof loadMainHarness>>;
let harness: MainHarness;

async function loadMainHarness() {
  vi.resetModules();
  const minecraft = await import("@minecraft/server");
  const gametest = await import("@minecraft/server-gametest");
  minecraft.resetMinecraftMocks();
  const testing = await import("../scripts/bridge/testing");
  await testing.resetBridgeRouterForTests();
  await import("../scripts/main");
  const runCommand = vi.fn(async () => undefined);
  const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  minecraft.world.getAllPlayers.mockReturnValue([
    {
      name: "MCBEWS_BRIDGE",
      runCommand,
      id: "tool1",
      location: { x: 0, y: 0, z: 0 },
      dimension: { id: "overworld" },
      getComponent: vi.fn(),
      getTags: vi.fn(() => []),
      getGameMode: vi.fn(),
    },
  ]);
  return { minecraft, gametest, testing, runCommand, warnSpy };
}

function serverEvent(message: string) {
  return { id: BRIDGE_REQUEST_MESSAGE_ID, sourceType: "Server", message };
}

function v2Request(id: string): string {
  return JSON.stringify({ v: 2, request_id: id, capability: "missing", payload: {} });
}

beforeEach(async () => {
  harness = await loadMainHarness();
});

afterEach(async () => {
  await harness.testing.flushRouter();
  await harness.testing.resetBridgeRouterForTests();
  harness.minecraft.resetMinecraftMocks();
  harness.warnSpy.mockRestore();
});

it("turns an unsupported Server request into a response tell", async () => {
  harness.minecraft.world.afterEvents.worldLoad.emit({});
  harness.minecraft.system.afterEvents.scriptEventReceive.emit({
    id: BRIDGE_REQUEST_MESSAGE_ID,
    sourceType: "Server",
    message: JSON.stringify({ v: 2, request_id: "r1", capability: "missing", payload: {} }),
  });
  await harness.testing.flushRouter();
  expect(harness.runCommand).toHaveBeenCalledWith(expect.stringContaining("tell @s MCBEWS|BRIDGE|r1|"));
});

it("queues a pre-ready request and responds after activation", async () => {
  harness.minecraft.system.afterEvents.scriptEventReceive.emit(serverEvent(v2Request("early")));
  expect(harness.runCommand).not.toHaveBeenCalled();
  expect(harness.testing.getPreReadyQueueSize()).toBe(1);
  harness.minecraft.world.afterEvents.worldLoad.emit({});
  await harness.testing.flushRouter();
  expect(harness.runCommand).toHaveBeenCalledWith(expect.stringContaining("tell @s MCBEWS|BRIDGE|early|"));
  expect(harness.testing.getPreReadyQueueSize()).toBe(0);
});

it("bounds the pre-ready queue", async () => {
  for (let index = 0; index < MAX_PRE_READY_REQUESTS + 10; index += 1) {
    harness.minecraft.system.afterEvents.scriptEventReceive.emit(serverEvent(v2Request(`r-${index}`)));
  }
  expect(harness.testing.getPreReadyQueueSize()).toBe(MAX_PRE_READY_REQUESTS);
  expect(harness.runCommand).not.toHaveBeenCalled();
  expect(harness.warnSpy).toHaveBeenCalledWith(expect.stringContaining("BRIDGE_NOT_READY_QUEUE_FULL"));
  harness.minecraft.world.afterEvents.worldLoad.emit({});
  await harness.testing.flushRouter();
  expect(harness.runCommand).toHaveBeenCalledTimes(MAX_PRE_READY_REQUESTS);
  expect(harness.runCommand).not.toHaveBeenCalledWith(expect.stringContaining("MCBEWS|BRIDGE|r-64|"));
});

it("does not activate or execute handlers when tool initialization fails", async () => {
  harness.minecraft.world.getAllPlayers.mockReturnValue([]);
  harness.gametest.spawnSimulatedPlayer.mockImplementationOnce(() => {
    throw new Error("world not ready");
  });
  harness.minecraft.system.afterEvents.scriptEventReceive.emit(serverEvent(v2Request("retry")));
  harness.minecraft.world.afterEvents.worldLoad.emit({});
  await Promise.resolve();
  expect(harness.runCommand).not.toHaveBeenCalled();
  expect(harness.testing.getPreReadyQueueSize()).toBe(1);
});

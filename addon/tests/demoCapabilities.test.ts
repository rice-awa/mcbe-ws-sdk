import { beforeEach, describe, expect, it, vi } from "vitest";
import { system, world, type Player, type ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import {
  activateBridge,
  enqueueOrHandle,
  registerCapability,
  registerV2Stream,
  _testingFlush,
  _testingReset,
} from "../scripts/bridge/router";
import { registerDemoCapabilities } from "../scripts/demoCapabilities";
import { MANIFEST } from "../scripts/bridge/v2/assets";
import { Message } from "../scripts/bridge/v2/codec";
import { fragment, ReassemblyPool } from "../scripts/bridge/v2/transport";

function event(id: string, message: string): ScriptEventCommandMessageAfterEvent {
  return { id, message, sourceType: "Entity" } as ScriptEventCommandMessageAfterEvent;
}

let sent: string[];
let epoch: string;
let sequence = 0;

function gameMessage(frame: string): string {
  // Bedrock expands selector mentions in both scriptevent and tell messages.
  return frame.replace(/@[aesprn](?:\[[^\]]*\])?/g, "Alex, 猪, 牛");
}

async function setup(): Promise<void> {
  _testingReset();
  sequence = 0;
  registerDemoCapabilities();
  sent = [];
  await activateBridge(async (frame) => {
    sent.push(frame);
  });
  enqueueOrHandle(event(MANIFEST.negotiation.event, "MCBEWS2H|demo"));
  epoch = sent[0].split("|")[2];
  enqueueOrHandle(event(MANIFEST.negotiation.event, `MCBEWS2C|demo|${epoch}`));
}

async function call(method: string, params: Record<string, unknown>): Promise<Message> {
  const id = `p.${epoch}.1`;
  const tid = `p.${epoch}.${++sequence}`;
  for (const frame of fragment({ v: "MCBEWS/2", kind: "request", id, method, params }, tid, "down")) {
    enqueueOrHandle(event(MANIFEST.carrier.event, gameMessage(frame)));
  }
  await _testingFlush();
  const pool = new ReassemblyPool();
  const response = sent
    .filter((frame) => frame.startsWith("MCBEWS2|"))
    .map((frame) => pool.receive("c", "s", gameMessage(frame), "up"))
    .find((message) => message !== null);
  if (!response) throw new Error("no response");
  return response;
}

beforeEach(async () => {
  world.getAllPlayers.mockReset().mockReturnValue([]);
  world.getDimension.mockReset();
  system.afterEvents.scriptEventReceive.clear();
  await setup();
});

describe("demo capabilities", () => {
  it("registers demo.echo and echoes metadata", async () => {
    const response = await call("demo.echo", { text: "hi" });
    expect(response.result).toEqual({ text: "hi", metadata: {} });
  });

  it("echoes literal selectors through both Minecraft message parsers", async () => {
    const text = "@e[type=minecraft:cow] @a @p @s 玩家😀";
    const response = await call("demo.echo", { text });
    expect(response.result).toEqual({ text, metadata: {} });
  });

  it("registers both the echo and the stream names", () => {
    expect(() => registerCapability("demo.echo", () => ({}))).toThrow(/already registered/);
    expect(() => registerV2Stream("demo.ticks", async function* () {})).toThrow(/already registered/);
    expect(() => registerCapability("run_world_command", () => ({}))).toThrow(/already registered/);
  });

  it("keeps the world-command allowlist and returns a structured rejection", async () => {
    const response = await call("run_world_command", { command: "setblock ~ ~ ~ stone" });
    expect(response.error).toEqual({
      code: "INVALID_PARAMS",
      message: "命令 setblock 不允许通过 addon 执行",
    });
  });

  it("rejects a non-string command as a parameter error", async () => {
    const response = await call("run_world_command", { command: 42 });
    expect(response.error).toEqual({
      code: "INVALID_PARAMS",
      message: "payload.command must be a string",
    });
  });

  it("executes an allowed world command and reports the result", async () => {
    const runCommand = vi.fn(() => ({ successCount: 1 }));
    world.getDimension.mockReturnValue({ runCommand });
    const response = await call("run_world_command", { command: "say hi" });
    expect(runCommand).toHaveBeenCalledWith("say hi");
    expect(response.result).toEqual({
      ok: true,
      payload: { output: "命令执行成功", successCount: 1 },
    });
  });

  it("reports zero successful applications without claiming command success", async () => {
    world.getDimension.mockReturnValue({ runCommand: vi.fn(() => ({ successCount: 0 })) });
    const response = await call("run_world_command", { command: "kill cow" });
    expect(response.result).toEqual({
      ok: true,
      payload: { output: "命令已返回，成功应用次数为 0", successCount: 0 },
    });
  });

  it("executes the original selector after the scriptevent hop", async () => {
    const runCommand = vi.fn(() => ({ successCount: 3 }));
    world.getDimension.mockReturnValue({ runCommand });
    const response = await call("run_world_command", { command: "kill @e" });
    expect(runCommand).toHaveBeenCalledWith("kill @e");
    expect(response.result).toEqual({ ok: true, payload: { output: "命令执行成功", successCount: 3 } });
  });

  it("runs selectors and relative coordinates in the explicitly named player's context", async () => {
    const runCommand = vi.fn(() => ({ successCount: 1 }));
    world.getAllPlayers.mockReturnValue([{ name: "玩家 Alex", runCommand } as unknown as Player]);
    const response = await call("run_world_command", { command: "tp @s ~ ~1 ~", player: "玩家 Alex" });
    expect(response.error).toBeUndefined();
    expect(runCommand).toHaveBeenCalledWith("tp @s ~ ~1 ~");
    expect(world.getDimension).not.toHaveBeenCalled();
  });

  it("rejects an unavailable player without falling back to the world context", async () => {
    const response = await call("run_world_command", { command: "say hi", player: "Offline" });
    expect(response.error).toEqual({ code: "INVALID_PARAMS", message: "未找到在线玩家：Offline" });
    expect(world.getDimension).not.toHaveBeenCalled();
  });

  it.each([42, null, "", "   ", "@a"])("rejects invalid or nonliteral player %s", async (player) => {
    const response = await call("run_world_command", { command: "say hi", player });
    expect(response.error).toBeDefined();
    expect(world.getDimension).not.toHaveBeenCalled();
  });

  it("keeps the command denylist when a player context is supplied", async () => {
    const runCommand = vi.fn(() => ({ successCount: 1 }));
    world.getAllPlayers.mockReturnValue([{ name: "Alex", runCommand } as unknown as Player]);
    const response = await call("run_world_command", { command: "setblock ~ ~ ~ stone", player: "Alex" });
    expect(response.error).toEqual({ code: "INVALID_PARAMS", message: "命令 setblock 不允许通过 addon 执行" });
    expect(runCommand).not.toHaveBeenCalled();
  });
});

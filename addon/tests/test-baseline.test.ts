import { describe, expect, it, vi } from "vitest";
import { system, world } from "@minecraft/server";

describe("Minecraft test baseline", () => {
  it("emits controllable world and script events", () => {
    const onWorldLoad = vi.fn();
    world.afterEvents.worldLoad.subscribe(onWorldLoad);
    world.afterEvents.worldLoad.emit({});
    expect(onWorldLoad).toHaveBeenCalledOnce();
    expect(system.afterEvents.scriptEventReceive).toBeDefined();
  });
});

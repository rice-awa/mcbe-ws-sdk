import { describe, expect, it } from "vitest";
import { defaultCapabilityRegistry, handleRunWorldCommand } from "../scripts/bridge/capabilities";

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

  it("contains exactly two default capabilities", () => {
    const keys = Object.keys(defaultCapabilityRegistry);
    expect(keys).toHaveLength(2);
    expect(keys.sort()).toEqual(["get_inventory_snapshot", "get_player_snapshot"]);
  });
});

describe("handleRunWorldCommand export", () => {
  it("is still exported for explicit host registration", () => {
    expect(handleRunWorldCommand).toBeDefined();
    expect(typeof handleRunWorldCommand).toBe("function");
  });
});

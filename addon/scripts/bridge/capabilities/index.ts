import type { CapabilityHandler, CapabilityValidator } from "../capabilityRegistry";
import { CapabilityRegistry } from "../capabilityRegistry";

import { handleGetPlayerSnapshot } from "./getPlayerSnapshot";
import { handleGetInventorySnapshot } from "./getInventorySnapshot";
import { handleRunWorldCommand } from "./runWorldCommand";

/** Reject a non-string `target` before the handler touches world state. */
const validateTarget: CapabilityValidator = (payload) =>
  payload.target === undefined || typeof payload.target === "string" ? null : "payload.target must be a string";

// 默认能力表（旧入口，形状与语义保持不变）。新的运行时注册请使用
// `registerCapability`；该对象保留给按源码追加条目的旧用法与兼容检查。
export const defaultCapabilityRegistry: Record<string, CapabilityHandler> = {
  get_player_snapshot: (_capability, payload, _context) => handleGetPlayerSnapshot(payload),
  get_inventory_snapshot: (_capability, payload, _context) => handleGetInventorySnapshot(payload),
};

/** Add the built-in capabilities to *registry* (used for creation and test reset). */
export function registerDefaultCapabilities(registry: CapabilityRegistry): void {
  registry.register("get_player_snapshot", defaultCapabilityRegistry.get_player_snapshot, {
    validate: validateTarget,
  });
  registry.register("get_inventory_snapshot", defaultCapabilityRegistry.get_inventory_snapshot, {
    validate: validateTarget,
  });
}

/** Build a registry containing only the built-in capabilities. */
export function createDefaultCapabilityRegistry(): CapabilityRegistry {
  const registry = new CapabilityRegistry();
  registerDefaultCapabilities(registry);
  return registry;
}

export { handleGetPlayerSnapshot } from "./getPlayerSnapshot";
export { handleGetInventorySnapshot } from "./getInventorySnapshot";
export { handleRunWorldCommand } from "./runWorldCommand";

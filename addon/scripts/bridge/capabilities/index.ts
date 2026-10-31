import type { CapabilityHandler, CapabilityValidator } from "../capabilityRegistry";
import { CapabilityRegistry } from "../capabilityRegistry";

import { handleGetPlayerSnapshot } from "./getPlayerSnapshot";
import { handleGetInventorySnapshot } from "./getInventorySnapshot";
import { handleRunWorldCommand } from "./runWorldCommand";

/** Reject a non-string `target` before the handler touches world state. */
const validateTarget: CapabilityValidator = (payload) =>
  payload.target === undefined || typeof payload.target === "string" ? null : "payload.target must be a string";

// 默认能力表（旧入口，形状与语义保持不变）。注册表按本表当前的键注册，
// 并在每次分发时从本表读取处理器；新增代码请优先使用 registerCapability。
export const defaultCapabilityRegistry: Record<string, CapabilityHandler> = {
  get_player_snapshot: (_capability, payload, _context) => handleGetPlayerSnapshot(payload),
  get_inventory_snapshot: (_capability, payload, _context) => handleGetInventorySnapshot(payload),
};

const defaultValidators: Partial<Record<string, CapabilityValidator>> = {
  get_player_snapshot: validateTarget,
  get_inventory_snapshot: validateTarget,
};

/** Register entries currently in the exported default table. */
export function registerDefaultCapabilities(registry: CapabilityRegistry): void {
  for (const name of Object.keys(defaultCapabilityRegistry)) {
    registry.register(
      name,
      (capability, payload, handlerContext) => {
        const handler = defaultCapabilityRegistry[capability];
        if (!handler) {
          return {
            ok: false,
            error: { code: "UNSUPPORTED_CAPABILITY", message: `unsupported capability: ${capability}` },
          };
        }
        return handler(capability, payload, handlerContext);
      },
      { validate: defaultValidators[name] }
    );
  }
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

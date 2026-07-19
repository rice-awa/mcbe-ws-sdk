import type { CapabilityHandler } from "../router";

import { handleGetPlayerSnapshot } from "./getPlayerSnapshot";
import { handleGetInventorySnapshot } from "./getInventorySnapshot";
import { handleRunWorldCommand } from "./runWorldCommand";

// 默认能力注册表。router.ts 在宿主未通过 setCapabilityHandler 注入处理器时，
// 回退到本表提供开箱即用的基础能力；宿主注册处理器后会完全覆盖默认表。
//
// 内置 handler 只接受 payload，而 CapabilityHandler 签名为 (capability, payload)，
// 故在此用适配器抹平第一个 capability 参数。
export const defaultCapabilityRegistry: Record<string, CapabilityHandler> = {
  get_player_snapshot: (_capability, payload) => handleGetPlayerSnapshot(payload),
  get_inventory_snapshot: (_capability, payload) => handleGetInventorySnapshot(payload),
  run_world_command: (_capability, payload) => handleRunWorldCommand(payload),
};

export { handleGetPlayerSnapshot } from "./getPlayerSnapshot";
export { handleGetInventorySnapshot } from "./getInventorySnapshot";
export { handleRunWorldCommand } from "./runWorldCommand";

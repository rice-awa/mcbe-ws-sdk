import { world } from "@minecraft/server";

import { findDisallowedCommand } from "./commandSafety";

/**
 * Default policy: no allowlist, so every entrypoint outside the
 * dangerous-command denylist runs. Pass an allowlist to narrow it further.
 */
const DEFAULT_ALLOWLIST: string[] | null = null;

/** Run as an exact online player, or in the overworld when player is omitted. */
export function handleRunWorldCommand(
  payload: { command?: string; player?: string },
  allowlist: string[] | null = DEFAULT_ALLOWLIST
): {
  ok: boolean;
  payload: { output: string; successCount: number };
} {
  const command = payload.command?.trim() ?? "";
  if (!command) {
    return {
      ok: false,
      payload: { output: "命令不能为空", successCount: 0 },
    };
  }

  const disallowedCommand = findDisallowedCommand(command, allowlist);
  if (disallowedCommand) {
    return {
      ok: false,
      payload: { output: `命令 ${disallowedCommand} 不允许通过 addon 执行`, successCount: 0 },
    };
  }

  try {
    const player =
      payload.player === undefined ? undefined : world.getAllPlayers().find((entry) => entry.name === payload.player);
    if (payload.player !== undefined && !player) {
      return {
        ok: false,
        payload: { output: `未找到在线玩家：${payload.player}`, successCount: 0 },
      };
    }
    const result = player ? player.runCommand(command) : world.getDimension("overworld").runCommand(command);
    return {
      ok: true,
      payload: {
        output: result.successCount === 0 ? "命令已返回，成功应用次数为 0" : "命令执行成功",
        successCount: result.successCount,
      },
    };
  } catch (error) {
    return {
      ok: false,
      payload: {
        output: error instanceof Error ? error.message : String(error),
        successCount: 0,
      },
    };
  }
}

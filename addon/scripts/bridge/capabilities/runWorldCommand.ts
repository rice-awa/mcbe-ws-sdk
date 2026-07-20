import { world } from "@minecraft/server";

import { findDisallowedCommand } from "./commandSafety";

/** Default allowlist: only `say` is permitted through the bridge. */
const DEFAULT_ALLOWLIST = ["say"];

export function handleRunWorldCommand(
  payload: { command?: string },
  allowlist: string[] | null = DEFAULT_ALLOWLIST,
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
    const result = world.getDimension("overworld").runCommand(command);
    return {
      ok: true,
      payload: {
        output: "命令执行成功",
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

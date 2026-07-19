import { system, world } from "@minecraft/server";
import type { Player } from "@minecraft/server";

import { BRIDGE_RESPONSE_PREFIX, BRIDGE_MAX_CHUNK_CONTENT_LENGTH } from "./constants";
import { chunkBridgePayload } from "./chunking";

const TAG = "bridge_tool";

let initialized = false;

/**
 * 确保存在一个被标记为 `bridge_tool` 的玩家实体，并在首次调用时加载相关维度。
 * 如果世界尚未就绪（getAllPlayers 为空且 playSound 不可用）则跳过 — 由 worldLoad 重试。
 */
export function ensureToolPlayer(): void {
  if (initialized) {
    return;
  }

  try {
    const players = world.getAllPlayers() || [];
    if (players.length === 0) {
      return; // 世界尚未就绪 — worldLoad 回调会重试
    }

    // 如果已有带 tag 的玩家，复用
    for (const p of players) {
      if (p.hasTag(TAG)) {
        initialized = true;
        return;
      }
    }

    // 给第一个在线玩家打上 bridge_tool 标签
    players[0].addTag(TAG);
    world.getDimension("overworld").runCommand(`tellraw @a[tag=${TAG}] {"rawtext":[{"text":"MCBE bridge tool ready"}]}`);
    initialized = true;
  } catch {
    // 世界未就绪时静默跳过
  }
}

function findToolPlayer(): Player | undefined {
  const players = world.getAllPlayers();
  return players.find((p) => p.hasTag(TAG));
}

/**
 * 将桥接响应编码为 MCBEAI|RESP 分片，以 MCBEAI_TOOL 玩家身份发送 tellraw。
 *
 * 与 Python SDK `AddonBridgeService` 的 `MCBEAI|RESP` 格式完全匹配。
 */
export async function sendBridgeResponseChunks(requestId: string, jsonBody: string): Promise<void> {
  const chunks = chunkBridgePayload(requestId, jsonBody, BRIDGE_MAX_CHUNK_CONTENT_LENGTH);
  const toolPlayer = findToolPlayer();
  if (!toolPlayer) {
    console.warn(`[bridge] 无法发送响应: 没有持有 ${TAG} tag 的在线玩家`);
    return;
  }

  const dimension = world.getDimension("overworld");

  for (const chunk of chunks) {
    const escaped = chunk.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    const cmd = `tellraw @a[name="${toolPlayer.name}"] {"rawtext":[{"text":"${escaped}"}]}`;
    await new Promise<void>((resolve) => {
      try {
        dimension.runCommand(cmd);
      } catch {
        // 忽略单条tellraw失败
      }
      // system.run 延迟确保命令间不堆积
      system.runTimeout(() => resolve(), 1);
    });
  }
}

import { GameMode, world, system } from "@minecraft/server";
import { spawnSimulatedPlayer } from "@minecraft/server-gametest";

import { MANIFEST } from "./v2/assets";

const DEBUG = true;

function log(message: string): void {
  if (DEBUG) {
    console.log(`[mcbews-bridge-sender] ${message}`);
  }
}

const TOOL_PLAYER_DIMENSION = "overworld";
const TOOL_PLAYER_LOCATION = { x: 300000, y: 100, z: 300000 };
const TOOL_PLAYER_CHECK_INTERVAL_TICKS = 20 * 30;
const TRUSTED_BRIDGE_PLAYER_NAME = MANIFEST.carrier.trusted_player;

let isToolPlayerInitialized = false;

export function ensureToolPlayer(): void {
  log(`ensureToolPlayer: 检查模拟玩家 ${TRUSTED_BRIDGE_PLAYER_NAME} 是否存在...`);

  const existing = world.getAllPlayers().find((player) => player.name === TRUSTED_BRIDGE_PLAYER_NAME);

  if (existing) {
    log("ensureToolPlayer: 模拟玩家已存在，跳过创建");
    return;
  }

  log(
    `ensureToolPlayer: 模拟玩家不存在，尝试在 (${TOOL_PLAYER_LOCATION.x}, ${TOOL_PLAYER_LOCATION.y}, ${TOOL_PLAYER_LOCATION.z}) 创建...`
  );
  try {
    const dimension = world.getDimension(TOOL_PLAYER_DIMENSION);
    log(`ensureToolPlayer: 获取维度 ${TOOL_PLAYER_DIMENSION} 成功`);

    const player = spawnSimulatedPlayer(
      {
        dimension,
        ...TOOL_PLAYER_LOCATION,
      },
      TRUSTED_BRIDGE_PLAYER_NAME,
      GameMode.Creative
    );
    log(`ensureToolPlayer: 模拟玩家创建成功: ${player.name}`);
  } catch (error) {
    log(`ensureToolPlayer: 模拟玩家创建失败: ${error instanceof Error ? error.message : String(error)}`);
    throw error;
  }
}

/** Send one already bounded MCBEWS/2 carrier frame through the bridge player. */
export async function sendV2Frame(frame: string): Promise<void> {
  const player = world.getAllPlayers().find((entry) => entry.name === TRUSTED_BRIDGE_PLAYER_NAME);
  if (!player) throw new Error("Tool player is not available");
  await player.runCommand(`tell @s ${frame}`);
}

export function initializeToolPlayer(): void {
  log("initializeToolPlayer: 开始初始化...");

  if (isToolPlayerInitialized) {
    log("initializeToolPlayer: 已初始化过，跳过");
    return;
  }

  log("initializeToolPlayer: 调用 ensureToolPlayer...");
  ensureToolPlayer();

  isToolPlayerInitialized = true;
  log("initializeToolPlayer: 初始化完成，设置定时检查...");

  system.runInterval(() => {
    ensureToolPlayer();
  }, TOOL_PLAYER_CHECK_INTERVAL_TICKS);
}

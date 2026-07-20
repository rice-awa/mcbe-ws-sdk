import { world, system } from "@minecraft/server";
import { registerBridgeRouter } from "./bridge/router";
import { registerResponseSyncHandler } from "./bridge/responseSync";

const DEBUG = true;

function log(message: string): void {
  if (DEBUG) {
    console.log(`[mcbe-ws-bridge] ${message}`);
  }
}

/**
 * 早期执行阶段：仅注册事件订阅，不访问 world 状态。
 * 在你的 addon main 入口中率先调用。
 */
export function initializeEarly(): void {
  log("initializeEarly: 注册桥接事件...");
  registerBridgeRouter();
  registerResponseSyncHandler();
  log("initializeEarly: 完成");
}

/**
 * 世界加载后初始化：需要 world 状态的代码在此回调内执行。
 * 双重策略：
 * 1. 订阅 worldLoad 事件 — 覆盖首次启动场景（世界尚未加载）
 * 2. system.run 兜底 — 覆盖 /reload 场景（世界已加载，worldLoad 不再触发）
 *
 * onReady 仅在首次成功后标记完成；失败时保留重试机会（例如 system.run 过早、
 * worldLoad 再触发一次）。initializeToolPlayer / activateBridge 自身也有幂等保护。
 *
 * 用法：在你的 addon main 入口中，`initializeEarly()` 之后调用 `initializeAfterWorldLoad()`。
 * 你的宿主代码在此处注册能力处理器和 AI 响应处理器。
 */
export function initializeAfterWorldLoad(onReady?: () => void | Promise<void>): void {
  log("initializeAfterWorldLoad: 等待世界就绪...");

  let completed = false;
  let inFlight = false;

  const init = async (source: string): Promise<void> => {
    if (completed) {
      log(`initializeAfterWorldLoad: 已完成，忽略重复触发 (${source})`);
      return;
    }
    if (inFlight) {
      log(`initializeAfterWorldLoad: 初始化进行中，忽略 (${source})`);
      return;
    }

    inFlight = true;
    log(`initializeAfterWorldLoad: 世界就绪 (${source})`);
    try {
      await onReady?.();
      completed = true;
      log(`initializeAfterWorldLoad: onReady 完成 (${source})`);
    } catch (error) {
      // 不置 completed —— 留给另一条路径（worldLoad / 后续重试）再试
      console.warn(
        `[bridge] initialization failed (${source})`,
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      inFlight = false;
    }
  };

  world.afterEvents.worldLoad.subscribe(() => {
    log("worldLoad 事件触发");
    void init("worldLoad");
  });

  // /reload 兜底：世界已加载时，system.run 在下一个 tick 尝试初始化
  system.run(() => {
    log("system.run 兜底: 尝试立即初始化...");
    void init("system.run");
  });
}

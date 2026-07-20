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
 * 订阅 worldLoad 事件 + system.run 兜底，覆盖首次启动和 /reload 两种场景。
 *
 * 用法：在你的 addon main 入口中，`initializeEarly()` 之后调用 `initializeAfterWorldLoad()`。
 * 你的宿主代码在此处注册能力处理器和 AI 响应处理器。
 */
export function initializeAfterWorldLoad(onReady?: () => void | Promise<void>): void {
  log("initializeAfterWorldLoad: 等待世界就绪...");

  const init = async (): Promise<void> => {
    try {
      await onReady?.();
    } catch (error) {
      console.warn("[bridge] initialization failed", error);
    }
  };

  world.afterEvents.worldLoad.subscribe(() => {
    void init();
  });
  system.run(() => {
    void init();
  });
}

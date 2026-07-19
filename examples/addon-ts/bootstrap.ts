/**
 * Minimal addon bootstrap lifecycle.
 *
 * Usage from your behaviour-pack entry script::
 *
 *   import { initializeEarly } from "./bootstrap";
 *   import { initializeAfterWorldLoad } from "./bootstrap";
 *
 *   initializeEarly();
 *   // ... your pack init ...
 *   initializeAfterWorldLoad();
 *
 * NOTE: This example does NOT handle tool-player setup or UI entry.
 * A production addon must also create a fake player for tool
 * execution and register UI form callbacks.  See the full
 * reference addon implementation for those concerns.
 */

import { system, world } from "@minecraft/server";

import { registerAiRespSyncHandler } from "./handler";

/** Set to ``false`` to suppress debug logging. */
const DEBUG = true;

function log(message: string): void {
  if (DEBUG) {
    console.log(`[addon-ts-example] ${message}`);
  }
}

/**
 * Early initialisation — safe to call before the world is loaded.
 *
 * Registers event subscriptions that do not access world state
 * (players, dimensions, entities, etc.).
 */
export function initializeEarly(): void {
  log("initializeEarly: registering event subscriptions");
  registerAiRespSyncHandler();
  log("initializeEarly: done");
}

/**
 * Delayed initialisation that requires a loaded world.
 *
 * Uses two strategies so it works both on first start and after
 * ``/reload``:
 *
 * 1.  ``world.afterEvents.worldLoad`` for cold starts.
 * 2.  ``system.run`` as a fallback when the world is already loaded.
 */
export function initializeAfterWorldLoad(): void {
  log("initializeAfterWorldLoad: scheduling world-ready init");

  const init = (): void => {
    log("initializeAfterWorldLoad: running world-ready init");
    // In a full addon you would create the tool player here.
    // This example intentionally skips it.
    log("initializeAfterWorldLoad: world-ready init complete");
  };

  world.afterEvents.worldLoad.subscribe(() => {
    log("worldLoad event fired");
    init();
  });

  system.run(() => {
    try {
      log("system.run fallback: attempting immediate init");
      init();
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      log(`system.run fallback: init failed, waiting for worldLoad (${msg})`);
    }
  });
}

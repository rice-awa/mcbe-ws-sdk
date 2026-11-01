import { system } from "@minecraft/server";
import { registerCapability, registerV2Stream } from "./bridge/router";
import { handleRunWorldCommand } from "./bridge/capabilities/runWorldCommand";

/**
 * Register the complete demo set used by the default `main.demo.ts` entry and
 * the example server.
 *
 * The default package ships these capabilities. `--base` selects `main.ts`,
 * which registers only the two read-only snapshots. The world command still
 * rejects every entrypoint on the shared dangerous-command denylist.
 */
export function registerDemoCapabilities(): void {
  registerCapability("demo.echo", (_method, payload, context) => ({
    text: payload.text,
    metadata: context.metadata,
  }));

  registerV2Stream("demo.ticks", async function* (_method, params, context) {
    const count = params.count;
    if (count !== undefined && (typeof count !== "number" || !Number.isInteger(count) || count < 1 || count > 100)) {
      throw new Error("count must be an integer from 1 to 100");
    }
    let index = 0;
    // An omitted count produces until the consumer cancels; no final length is needed.
    while (!context.cancellation?.cancelled && (count === undefined || index < count)) {
      await new Promise<void>((resolve) => {
        let unsubscribe = () => {};
        const handle = system.runTimeout(() => {
          unsubscribe();
          resolve();
        }, 10);
        unsubscribe =
          context.cancellation?.onCancel(() => {
            system.clearRun(handle);
            resolve();
          }) ?? (() => {});
      });
      if (context.cancellation?.cancelled) return;
      yield { index: index++, metadata: context.metadata };
    }
  });

  registerCapability("run_world_command", (_method, payload) => {
    if (payload.command !== undefined && typeof payload.command !== "string") {
      return { ok: false, error: { code: "INVALID_REQUEST", message: "payload.command must be a string" } };
    }
    if (payload.player !== undefined && (typeof payload.player !== "string" || !payload.player.trim())) {
      return {
        ok: false,
        error: { code: "INVALID_REQUEST", message: "payload.player must be a non-empty player name" },
      };
    }
    const result = handleRunWorldCommand(payload as { command?: string; player?: string });
    if (!result.ok) {
      return { ok: false, error: { code: "INVALID_REQUEST", message: result.payload.output } };
    }
    return { ok: true, payload: result.payload };
  });
}

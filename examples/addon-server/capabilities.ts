/** Copy to addon/scripts/demoCapabilities.ts and call registerDemoCapabilities() from main.ts. */
import { system } from "@minecraft/server";
import { registerCapability, registerV2Stream } from "./bridge/router";

/** Register examples explicitly; the SDK's default capabilities stay unchanged. */
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
        unsubscribe = context.cancellation?.onCancel(() => {
          system.clearRun(handle);
          resolve();
        }) ?? (() => {});
      });
      if (context.cancellation?.cancelled) return;
      yield { index: index++, metadata: context.metadata };
    }
  });
}

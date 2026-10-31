const fs = require("node:fs");
const readline = require("node:readline");
const Module = require("node:module");
const ts = require("typescript");

require.extensions[".ts"] = (mod, file) => {
  const source = fs.readFileSync(file, "utf8");
  mod._compile(
    ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } })
      .outputText,
    file
  );
};
const originalLoad = Module._load;
Module._load = function (name, ...args) {
  if (name === "@minecraft/server") {
    return {
      system: {
        afterEvents: { scriptEventReceive: { subscribe() {} } },
        runTimeout: (callback, ticks) => setTimeout(callback, ticks * 50),
        runInterval: (callback, ticks) => setInterval(callback, ticks * 50),
        clearRun: (handle) => clearTimeout(handle),
      },
      world: {},
      EntityComponentTypes: {},
    };
  }
  return originalLoad.call(this, name, ...args);
};
console.log = (...args) => console.error(...args);
console.warn = (...args) => console.error(...args);
const router = require("../scripts/bridge/router.ts");
const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");

router.registerCapability("ts.echo", async (_name, payload, context) => ({
  payload,
  metadata: context.metadata,
  caller: context.caller,
}));
router.registerCapability("ts.reverse", async (_name, payload, context) => {
  const response = await context.client.request("py.echo", payload, { metadata: { child: { active: true } } });
  return { response, metadata: context.metadata };
});
router.registerCapability("ts.cycle", async (_name, payload, context) => {
  await context.client.request("py.cycle", payload);
  return {};
});
router.registerCapability("ts.notice", async (_name, payload, context) => {
  emit({ type: "notification", payload, metadata: context.metadata });
  return {};
});

async function main() {
  await router.activateBridge(async () => {});
  const endpoint = router.activateV2Bridge("test", async (frame) => emit({ type: "frame", frame }));
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  lines.on("line", (line) => {
    const item = JSON.parse(line);
    if (item.op === "frame") {
      router.enqueueOrHandle({ id: "mcbews:packet_v2", message: item.frame, sourceType: "Entity" });
    } else if (item.op === "request") {
      void endpoint
        .request(item.method, item.params, item.options)
        .then((message) => emit({ type: "result", key: item.key, message }))
        .catch((error) => emit({ type: "error", key: item.key, message: error.message }));
    } else if (item.op === "notify") {
      void endpoint
        .notify(item.method, item.params, item.options)
        .catch((error) => emit({ type: "fatal", error: error.message }));
    } else if (item.op === "close") {
      router._testingReset();
      lines.close();
    }
  });
  emit({ type: "ready" });
}
void main().catch((error) => {
  emit({ type: "fatal", error: error.message });
  process.exitCode = 1;
});

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

const streamGates = new Map();
const incomingStreams = new Map();
router.registerV2Stream("ts.stream", async function* (_name, payload, context) {
  yield { first: true, metadata: context.metadata };
  if (payload.gate) {
    await new Promise((resolve) => {
      streamGates.set(payload.gate, resolve);
      context.cancellation.onCancel(resolve);
    });
    streamGates.delete(payload.gate);
  }
  if (payload.large) yield "玩家😀".repeat(payload.large);
  if (payload.fail) throw new Error("producer failed");
});
router.registerV2Stream("ts.empty", async function* () {});

let readyEpoch = null;

function endpointFor(item) {
  const epoch = item.epoch ?? readyEpoch;
  const endpoint = epoch ? router.getV2Endpoint(epoch) : null;
  if (!endpoint) throw new Error(`no admitted peer for epoch ${epoch ?? "<none>"}`);
  return endpoint;
}

async function main() {
  const send = async (frame) => {
    const ready = /^MCBEWS2N\|[^|]+\|([A-Za-z0-9_-]{1,24})\|ready$/.exec(frame);
    if (ready) readyEpoch = ready[1];
    emit({ type: "frame", frame });
  };
  await router.activateBridge(send);
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  lines.on("line", (line) => {
    const item = JSON.parse(line);
    if (item.op === "frame") {
      router.enqueueOrHandle({ id: "mcbews:packet_v2", message: item.frame, sourceType: "Entity" });
    } else if (item.op === "hello") {
      router.enqueueOrHandle({ id: "mcbews:negotiate_v2", message: item.frame, sourceType: "Entity" });
    } else if (item.op === "request") {
      void endpointFor(item)
        .request(item.method, item.params, item.options)
        .then((message) => emit({ type: "result", key: item.key, message }))
        .catch((error) => emit({ type: "error", key: item.key, message: error.message }));
    } else if (item.op === "notify") {
      void endpointFor(item)
        .notify(item.method, item.params, item.options)
        .catch((error) => emit({ type: "fatal", error: error.message }));
    } else if (item.op === "stream") {
      void (async () => {
        try {
          const stream = await endpointFor(item).stream(item.method, item.params, item.options);
          incomingStreams.set(item.key, stream);
          for await (const value of stream) emit({ type: "stream-item", key: item.key, value });
          emit({ type: "stream-end", key: item.key });
        } catch (error) {
          emit({ type: "stream-error", key: item.key, code: error.code, message: error.message });
        } finally {
          incomingStreams.delete(item.key);
        }
      })();
    } else if (item.op === "stream_cancel") {
      incomingStreams.get(item.key)?.cancel();
    } else if (item.op === "stream_release") {
      streamGates.get(item.gate)?.();
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

// Cross-language smoke runners reuse this exact router instance instead of
// re-resolving the module through a loader hook.
module.exports = { router, getReadyEpoch: () => readyEpoch };

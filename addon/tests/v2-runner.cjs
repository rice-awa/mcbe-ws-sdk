// Actual TypeScript execution for Python/TypeScript contract probes (no Minecraft imports).
const fs = require("fs");
const ts = require("typescript");
require.extensions[".ts"] = (mod, file) => {
  const source = fs.readFileSync(file, "utf8");
  mod._compile(
    ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } })
      .outputText,
    file
  );
};
const codec = require("../scripts/bridge/v2/codec.ts");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const transport = require("../scripts/bridge/v2/transport.ts");
let now = 0;
let pool;
const result = input.map((item) => {
  try {
    if (item.op === "fragment")
      return { code: "ok", frames: transport.fragment(item.message, item.tid, item.direction, item.overrides) };
    if (item.op === "init") {
      now = 0;
      pool = new transport.ReassemblyPool(item.overrides, () => now);
      return { code: "ok", stats: pool.stats };
    }
    if (item.op === "receive")
      return {
        code: "ok",
        message: pool.receive(item.connection ?? "c", item.source ?? "s", item.frame, item.direction),
        stats: pool.stats,
      };
    if (item.op === "time") {
      now = item.now;
      pool.prune();
      return { code: "ok", stats: pool.stats };
    }
    if (item.op === "cancel") {
      pool.cancel(item.connection ?? "c", item.source ?? "s", item.tid);
      return { code: "ok", stats: pool.stats };
    }
    if (item.op === "close") {
      pool.close(item.connection ?? "c");
      return { code: "ok", stats: pool.stats };
    }
    const message = "wire" in item ? codec.decode(item.wire) : codec.validate(item.message);
    return { code: "ok", message, wire: codec.encode(message) };
  } catch (err) {
    if (!(err instanceof codec.V2ProtocolError)) throw err;
    return { code: err.code, ...(pool ? { stats: pool.stats } : {}) };
  }
});
process.stdout.write(JSON.stringify(result));

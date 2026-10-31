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
const result = input.map((item) => {
  try {
    const message = "wire" in item ? codec.decode(item.wire) : codec.validate(item.message);
    return { code: "ok", message, wire: codec.encode(message) };
  } catch (err) {
    if (!(err instanceof codec.V2ProtocolError)) throw err;
    return { code: err.code };
  }
});
process.stdout.write(JSON.stringify(result));

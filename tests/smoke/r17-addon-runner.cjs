// Load the real addon router and the installable example into the existing wire harness.
const Module = require("node:module");
const path = require("node:path");
require("../../addon/tests/v2-live.cjs");
const router = require("../../addon/scripts/bridge/router.ts");
const load = Module._load;
Module._load = function (name, parent, ...args) {
  if (name === "./bridge/router" && parent.filename.endsWith("capabilities.ts")) return router;
  return load.call(this, name, parent, ...args);
};
const register = router.registerV2Stream;
router.registerV2Stream = (name, handler) => register(name, async function* (...args) {
  try {
    yield* handler(...args);
  } finally {
    process.stdout.write(JSON.stringify({ type: "producer-stopped", cancelled: args[2].cancellation.cancelled }) + "\n");
  }
});
require(path.resolve(__dirname, "../../examples/addon-server/capabilities.ts")).registerDemoCapabilities();

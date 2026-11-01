// Load the real addon router and the installable example into the existing wire harness.
// v2-live.cjs installs the TypeScript loader and the @minecraft/server stub before it
// exports the shared router instance, so no extra module-load hook is needed here.
const { router } = require("../../addon/tests/v2-live.cjs");
const register = router.registerV2Stream;
router.registerV2Stream = (name, handler) => register(name, async function* (...args) {
  try {
    yield* handler(...args);
  } finally {
    process.stdout.write(JSON.stringify({ type: "producer-stopped", cancelled: args[2].cancellation.cancelled }) + "\n");
  }
});
require("../../examples/addon-server/capabilities.ts").registerDemoCapabilities();

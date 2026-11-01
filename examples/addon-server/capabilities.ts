/**
 * The example server and the demo addon entry share one registration source.
 *
 * The default addon build (`npm run mcaddon`) bundles
 * `addon/scripts/main.demo.ts`, which calls {@link registerDemoCapabilities}.
 * The lean `--base` build omits this set.
 */
export { registerDemoCapabilities } from "../../addon/scripts/demoCapabilities";

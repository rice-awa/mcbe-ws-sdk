/**
 * The example server and the demo addon entry share one registration source.
 *
 * Build the demo package from the addon directory with `npm run build:demo`
 * (or `just-scripts build --demo`); it bundles `addon/scripts/main.demo.ts`,
 * which calls {@link registerDemoCapabilities}.
 */
export { registerDemoCapabilities } from "../../addon/scripts/demoCapabilities";

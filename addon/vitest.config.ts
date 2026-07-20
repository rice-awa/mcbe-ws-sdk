import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@minecraft/server": fileURLToPath(new URL("./tests/__mocks__/minecraft-server.ts", import.meta.url)),
      "@minecraft/server-gametest": fileURLToPath(new URL("./tests/__mocks__/minecraft-server-gametest.ts", import.meta.url)),
      "@minecraft/server-ui": fileURLToPath(new URL("./tests/__mocks__/minecraft-server-ui.ts", import.meta.url)),
    },
  },
  test: {
    include: ["tests/**/*.test.ts"],
  },
});

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const scripts = fileURLToPath(new URL("../scripts", import.meta.url));
/** Generated vectors keep an invalid MCBEWS/1 sample on purpose. */
const generatedVectors = join(scripts, "bridge/v2/assets.ts");

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? files(path) : [path];
  });
}

const sources = files(scripts).filter((path) => path.endsWith(".ts"));

function source(name: string): string {
  return readFileSync(join(scripts, name), "utf-8");
}

describe("MCBEWS/2-only production surface", () => {
  it("does not keep the retired modules", () => {
    for (const retired of [
      "bridge/protocol.ts",
      "bridge/responseSync.ts",
      "bridge/testing.ts",
      "extensions/textResponse.ts",
    ]) {
      expect(existsSync(join(scripts, retired))).toBe(false);
    }
  });

  it("never names retired script events in production sources", () => {
    for (const path of sources) {
      const text = readFileSync(path, "utf-8");
      for (const id of ["mcbews:bridge_req", "mcbews:session_req", "mcbews:text_resp"]) {
        expect(text, `${path} must not reference ${id}`).not.toContain(id);
      }
    }
  });

  it("emits no retired chat prefixes or protocol line", () => {
    for (const path of sources) {
      const text = readFileSync(path, "utf-8");
      for (const marker of ["MCBEWS|BRIDGE", "MCBEWS|UI_CHAT", "MCBEWS|SESSION", "MCBEWS|TOOL_"]) {
        expect(text, `${path} must not emit ${marker}`).not.toContain(marker);
      }
      if (path !== generatedVectors) {
        expect(text, `${path} must not mention MCBEWS/1`).not.toContain("MCBEWS/1");
      }
    }
  });

  it("builds the demo entry by default and keeps the base entry lean", () => {
    const main = source("main.ts");
    const demo = source("main.demo.ts");
    expect(main).not.toContain("demoCapabilities");
    expect(main).not.toContain("run_world_command");
    expect(demo).toContain("registerDemoCapabilities");
    const demoSource = source("demoCapabilities.ts");
    for (const name of ["demo.echo", "demo.ticks", "run_world_command"]) {
      expect(demoSource).toContain(name);
    }
    const buildConfig = readFileSync(join(scripts, "..", "just.config.ts"), "utf-8");
    expect(buildConfig).toContain('argv()["base"]');
    expect(buildConfig).toContain("./scripts/main.demo.ts");
  });
});

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

type PackageJson = { dependencies: Record<string, string> };
type ModuleDependency = { module_name: string; version: string };
type PackDependency = { uuid: string; version: number[] };
type PackManifest = {
  header: { uuid: string; version: number[]; min_engine_version: number[] };
  modules: Array<{ uuid: string; version: number[] }>;
  dependencies?: Array<ModuleDependency | PackDependency>;
};

/**
 * Approved Script API baseline for the 1.26.30 game target.
 *
 * These are hard-coded instead of being compared between package.json and the
 * manifests: a cross-file comparison would still pass if every file were
 * downgraded together.
 */
const APPROVED_SERVER_MODULE = "2.8.0";
const APPROVED_SERVER_UI_MODULE = "2.1.0";

/** Beta modules are declared with the `1.0.0-beta` alias, which the engine resolves to its bundled beta build. */
const APPROVED_GAMETEST_MODULE = "1.0.0-beta";

/** npm type/runtime package paired with the 1.26.30 stable engine (beta channel). */
const APPROVED_GAMETEST_PACKAGE = "1.0.0-beta.1.26.30-stable";

const APPROVED_MIN_ENGINE_VERSION = [1, 26, 30];

/**
 * MCBEWS/1 identifiers removed by the V2-only migration. Pack manifests must not
 * advertise the legacy wire contract; the v2 carrier is `mcbews:packet_v2`.
 */
const V1_WIRE_IDENTIFIERS = [
  "MCBEWS/1",
  "mcbews:bridge_req",
  "mcbews:session_req",
  "mcbews:session_resp",
  "mcbews:text_resp",
  "MCBEWS|BRIDGE",
  "MCBEWS|UI_CHAT",
  "MCBEWS|SESSION",
  "MCBEWS|TOOL_APPROVE",
  "MCBEWS|TOOL_DENY",
];

const readText = (relativePath: string): string =>
  readFileSync(fileURLToPath(new URL(`../${relativePath}`, import.meta.url)), "utf-8");

const readJson = <T>(relativePath: string): T => JSON.parse(readText(relativePath)) as T;

const moduleVersions = (manifest: PackManifest): Record<string, string> =>
  Object.fromEntries(
    (manifest.dependencies ?? [])
      .filter((item): item is ModuleDependency => "module_name" in item && typeof item.module_name === "string")
      .map((item) => [item.module_name, item.version])
  );

const BEHAVIOR_MANIFEST = "behavior_packs/mc-ws-sdk/manifest.json";
const RESOURCE_MANIFEST = "resource_packs/mc-ws-sdk/manifest.json";

describe("Minecraft test baseline", () => {
  it("pins the approved Script API module versions", () => {
    const packageJson = readJson<PackageJson>("package.json");
    const behavior = readJson<PackManifest>(BEHAVIOR_MANIFEST);
    const moduleVersionsInPack = moduleVersions(behavior);

    expect(packageJson.dependencies["@minecraft/server"]).toBe(APPROVED_SERVER_MODULE);
    expect(packageJson.dependencies["@minecraft/server-ui"]).toBe(APPROVED_SERVER_UI_MODULE);
    expect(packageJson.dependencies["@minecraft/server-gametest"]).toBe(APPROVED_GAMETEST_PACKAGE);

    expect(moduleVersionsInPack["@minecraft/server"]).toBe(APPROVED_SERVER_MODULE);
    expect(moduleVersionsInPack["@minecraft/server-ui"]).toBe(APPROVED_SERVER_UI_MODULE);
    expect(moduleVersionsInPack["@minecraft/server-gametest"]).toBe(APPROVED_GAMETEST_MODULE);
  });

  it("targets the 1.26.30 engine in both packs", () => {
    const behavior = readJson<PackManifest>(BEHAVIOR_MANIFEST);
    const resource = readJson<PackManifest>(RESOURCE_MANIFEST);

    expect(behavior.header.min_engine_version).toEqual(APPROVED_MIN_ENGINE_VERSION);
    expect(resource.header.min_engine_version).toEqual(APPROVED_MIN_ENGINE_VERSION);
  });

  it("does not advertise MCBEWS/1 wire identifiers", () => {
    for (const manifestPath of [BEHAVIOR_MANIFEST, RESOURCE_MANIFEST]) {
      const contents = readText(manifestPath);
      for (const identifier of V1_WIRE_IDENTIFIERS) {
        expect(contents, `${manifestPath} must not contain ${identifier}`).not.toContain(identifier);
      }
    }
  });

  it("keeps pack identity and the behavior-to-resource dependency consistent", () => {
    const behavior = readJson<PackManifest>(BEHAVIOR_MANIFEST);
    const resource = readJson<PackManifest>(RESOURCE_MANIFEST);
    const resourceDependency = (behavior.dependencies ?? []).find((item): item is PackDependency => "uuid" in item);

    expect(behavior.header.version).toEqual([1, 0, 1]);
    expect(behavior.modules[0].version).toEqual([1, 0, 1]);
    expect(resource.header.version).toEqual([1, 0, 1]);
    expect(resource.modules[0].version).toEqual([1, 0, 1]);
    expect(behavior.header.uuid).toBe("60411f7f-774b-4269-b1cd-f58ebe702995");
    expect(resource.header.uuid).toBe("450332ea-6755-48b6-ab7f-36843065edd1");
    expect(resourceDependency).toEqual({
      uuid: resource.header.uuid,
      version: resource.header.version,
    });
  });
});

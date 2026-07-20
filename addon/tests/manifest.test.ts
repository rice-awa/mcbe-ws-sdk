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

const readJson = <T>(relativePath: string): T =>
  JSON.parse(
    readFileSync(
      fileURLToPath(new URL(`../${relativePath}`, import.meta.url)),
      "utf-8",
    ),
  ) as T;

describe("Minecraft test baseline", () => {
  it("keeps package and pack API versions consistent", () => {
    const packageJson = readJson<PackageJson>("package.json");
    const behavior = readJson<PackManifest>("behavior_packs/mc-ws-sdk/manifest.json");
    const resource = readJson<PackManifest>("resource_packs/mc-ws-sdk/manifest.json");
    const moduleVersions = Object.fromEntries(
      (behavior.dependencies ?? [])
        .filter(
          (item): item is ModuleDependency =>
            "module_name" in item && typeof item.module_name === "string",
        )
        .map((item) => [item.module_name, item.version]),
    );
    const resourceDependency = (behavior.dependencies ?? []).find(
      (item): item is PackDependency => "uuid" in item,
    );
    expect(packageJson.dependencies["@minecraft/server"]).toBe("2.0.0");
    expect(packageJson.dependencies["@minecraft/server-ui"]).toBe("2.0.0");
    expect(moduleVersions["@minecraft/server"]).toBe("2.0.0");
    expect(moduleVersions["@minecraft/server-ui"]).toBe("2.0.0");
    expect(packageJson.dependencies["@minecraft/server-gametest"])
      .toMatch(/^1\.0\.0-beta\./);
    expect(moduleVersions["@minecraft/server-gametest"]).toBe("1.0.0-beta");
    expect(behavior.header.min_engine_version).toEqual([1, 21, 80]);
    expect(resource.header.min_engine_version).toEqual([1, 21, 80]);
    expect(behavior.header.version).toEqual([1, 0, 0]);
    expect(behavior.modules[0].version).toEqual([1, 0, 0]);
    expect(resource.header.version).toEqual([1, 0, 0]);
    expect(resource.modules[0].version).toEqual([1, 0, 0]);
    expect(behavior.header.uuid).toBe("60411f7f-774b-4269-b1cd-f58ebe702995");
    expect(resource.header.uuid).toBe("450332ea-6755-48b6-ab7f-36843065edd1");
    expect(resourceDependency).toEqual({
      uuid: resource.header.uuid,
      version: resource.header.version,
    });
  });
});

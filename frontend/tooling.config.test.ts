import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

interface PackageManifest {
  private?: boolean;
  workspaces?: string[];
  scripts?: Record<string, string>;
  devDependencies?: Record<string, string>;
}

function readManifest(path: string): PackageManifest {
  return JSON.parse(readFileSync(path, "utf8")) as PackageManifest;
}

describe("frontend tooling isolation", () => {
  it("keeps TypeScript 5 peer tooling in a workspace away from the TypeScript 7 app", () => {
    const toolingPath = resolve(process.cwd(), "tooling/package.json");
    const root = readManifest(resolve(process.cwd(), "package.json"));

    expect(existsSync(toolingPath)).toBe(true);
    if (!existsSync(toolingPath)) {
      return;
    }
    const tooling = readManifest(toolingPath);

    expect(root.workspaces).toEqual(["tooling"]);
    expect(root.devDependencies?.typescript).toBe("7.0.2");
    expect(root.devDependencies?.["patch-package"]).toBeUndefined();
    expect(root.devDependencies?.["typescript-openapi"]).toBeUndefined();
    expect(root.scripts?.postinstall).toBeUndefined();
    expect(root.scripts?.["api:types"]).toContain(
      "--workspace @vibe-portfolio/tooling",
    );
    expect(root.scripts?.lint).toContain("--workspace @vibe-portfolio/tooling");

    expect(tooling.private).toBe(true);
    expect(tooling.devDependencies).toMatchObject({
      "@eslint/js": "10.0.1",
      eslint: "10.7.0",
      "eslint-plugin-react-hooks": "7.1.1",
      "eslint-plugin-react-refresh": "0.5.3",
      globals: "17.7.0",
      "openapi-typescript": "7.13.0",
      typescript: "5.9.3",
      "typescript-eslint": "8.64.0",
    });
  });
});

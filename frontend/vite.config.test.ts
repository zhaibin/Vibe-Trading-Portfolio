import { describe, expect, it } from "vitest";
import type { UserConfig } from "vitest/config";

import config from "./vite.config";

describe("frontend coverage contract", () => {
  it("automatically includes every production TypeScript source", () => {
    const coverage = (config as UserConfig).test?.coverage;

    expect(coverage?.include).toEqual(["src/**/*.{ts,tsx}"]);
    expect(coverage?.exclude).toEqual([
      "src/main.tsx",
      "src/api/schema.d.ts",
      "src/vite-env.d.ts",
      "src/**/*.test.{ts,tsx}",
      "src/**/*.types.ts",
    ]);
  });
});

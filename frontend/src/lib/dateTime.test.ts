import { describe, expect, it } from "vitest";

import { formatLocalDateTime } from "./dateTime";

describe("formatLocalDateTime", () => {
  it("shows a concise local date and minute while preserving parse safety", () => {
    const result = formatLocalDateTime("2026-07-20T08:14:49Z");

    expect(result).not.toContain("T");
    expect(result).not.toContain("Z");
    expect(result).not.toContain(":49");
    expect(result).toMatch(/2026/);
  });

  it("returns an explicit fallback for malformed values", () => {
    expect(formatLocalDateTime("not-a-date")).toBe("时间未知");
  });
});

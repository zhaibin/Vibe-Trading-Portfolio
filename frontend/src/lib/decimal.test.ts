import { describe, expect, it } from "vitest";

import { formatMoney, formatQuantity, ratioPercent } from "./decimal";

describe("decimal display formatting", () => {
  it("formats money to two decimal places without converting through Number", () => {
    expect(formatMoney("100465.000000", "CNY")).toBe("100465.00 CNY");
    expect(formatMoney("1.235000", "USD")).toBe("1.24 USD");
    expect(formatMoney("-0.005000", "HKD")).toBe("-0.01 HKD");
    expect(formatMoney("9007199254740993.995", "CNY")).toBe(
      "9007199254740994.00 CNY",
    );
  });

  it("limits percentages to two decimal places", () => {
    expect(ratioPercent("0.666666")).toBe("66.67%");
    expect(ratioPercent("0.25")).toBe("25%");
  });

  it("removes only insignificant zeroes from quantities", () => {
    expect(formatQuantity("100.00000000")).toBe("100");
    expect(formatQuantity("3.25000000")).toBe("3.25");
  });
});

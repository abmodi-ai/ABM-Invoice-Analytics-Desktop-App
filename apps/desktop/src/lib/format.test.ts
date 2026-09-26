import { compactMoney, display, money, pct } from "./format";

describe("format", () => {
  it("formats cents as dollars", () => {
    expect(money(12345)).toBe("$123.45");
    expect(money(-5)).toBe("-$0.05");
    expect(money(null)).toBe("—");
    expect(money(123456789)).toBe("$1,234,567.89");
  });
  it("compacts large amounts", () => {
    expect(compactMoney(1_234_567_00)).toBe("$1.2M");
    expect(compactMoney(45_000_00)).toBe("$45k");
  });
  it("renders percentages and empty values", () => {
    expect(pct(0.1234)).toBe("12.3%");
    expect(display([])).toBe("—");
    expect(display(["59", "RT"])).toBe("59, RT");
  });
});

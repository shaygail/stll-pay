import { describe, expect, it } from "vitest";
import { formatNZD, parseNZDToCents } from "./money";
import { readCookie } from "./api";

describe("formatNZD", () => {
  it("formats cents as dollars", () => {
    expect(formatNZD(1250)).toBe("$12.50");
    expect(formatNZD(5)).toBe("$0.05");
    expect(formatNZD(128450)).toBe("$1,284.50");
    expect(formatNZD(-300)).toBe("-$3.00");
  });

  it("refuses non-integer amounts", () => {
    expect(() => formatNZD(12.5)).toThrow(TypeError);
  });
});

describe("parseNZDToCents", () => {
  it.each([
    ["12", 1200],
    ["12.5", 1250],
    ["12.50", 1250],
    ["$1,234.56", 123456],
    [" 0.10 ", 10],
    ["0.29", 29], // 0.29 * 100 is 28.999… in floating point; must not round down
  ])("parses %s", (input, cents) => {
    expect(parseNZDToCents(input)).toBe(cents);
  });

  it.each(["", "0", "0.00", "-5", "12.345", "abc", "1e3", "12.5.0", "10000000"])(
    "rejects %s",
    (input) => {
      expect(parseNZDToCents(input)).toBeNull();
    },
  );
});

describe("readCookie", () => {
  it("finds a cookie by name", () => {
    expect(readCookie("stllpay_csrf", "a=1; stllpay_csrf=abc%3D; b=2")).toBe("abc=");
    expect(readCookie("missing", "a=1")).toBeUndefined();
  });
});

import { describe, expect, it } from "vitest";
import { extent, maxOf, minOf } from "@/lib/scale";

// Empty inputs must never produce Infinity (it rendered as "Infinity%" in the matrix legend).
describe("safe scales", () => {
  it("falls back on empty input", () => {
    expect(extent([])).toEqual([0, 1]);
    expect(maxOf([], 0.1)).toBe(0.1);
    expect(minOf([])).toBe(0);
  });
  it("ignores non-finite values", () => {
    expect(extent([NaN, 0.2, Infinity, 0.1])).toEqual([0.1, 0.2]);
  });
});

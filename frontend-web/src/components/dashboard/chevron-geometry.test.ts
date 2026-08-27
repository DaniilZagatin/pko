import { describe, expect, it } from "vitest";
import { computeChevronGeometry } from "./chevron-geometry";

describe("computeChevronGeometry", () => {
  it("matches the real slide's shape (w=220,h=90,adj=37356)", () => {
    const { x1, x2 } = computeChevronGeometry(220, 90, 37356);
    // ss=90, x1 = 90*37356/100000 = 33.6204
    expect(x1).toBeCloseTo(33.62, 1);
    expect(x2).toBeCloseTo(220 - 33.62, 1);
  });

  it("is symmetric at the default adj (50000)", () => {
    const { x1, x2 } = computeChevronGeometry(200, 100);
    // ss=100, x1 = 100*50000/100000 = 50
    expect(x1).toBeCloseTo(50, 5);
    expect(x2).toBeCloseTo(150, 5);
  });

  it("clamps adj above maxAdj instead of producing a negative x2", () => {
    // w=100,h=100 -> ss=100, maxAdj=100000*100/100=100000 -> adj 999999 clamps to 100000
    const { x1, x2 } = computeChevronGeometry(100, 100, 999999);
    expect(x1).toBeCloseTo(100, 5);
    expect(x2).toBeCloseTo(0, 5);
  });

  it("clamps negative adj to 0", () => {
    const { x1 } = computeChevronGeometry(200, 100, -500);
    expect(x1).toBe(0);
  });

  it("produces a closed 6-point polygon string", () => {
    const { points } = computeChevronGeometry(220, 90, 37356);
    const pairs = points.split(" ");
    expect(pairs).toHaveLength(6);
  });
});

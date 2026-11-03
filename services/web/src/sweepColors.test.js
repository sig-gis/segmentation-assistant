import { describe, expect, it } from "vitest";

import { SWEEP_PALETTE, sweepColor } from "./sweepColors.js";

describe("sweepColor", () => {
  it("picks the same palette color for a sweep every time", () => {
    const color = sweepColor("8e04e6c1-0000", {});
    expect(SWEEP_PALETTE).toContain(color);
    expect(sweepColor("8e04e6c1-0000", {})).toBe(color);
  });
  it("uses the color the user chose", () => {
    expect(sweepColor("a", { a: "#123456" })).toBe("#123456");
  });
  it("does not use the yellow of unreviewed suggestions", () => {
    expect(SWEEP_PALETTE).not.toContain("#f5b700");
  });
});

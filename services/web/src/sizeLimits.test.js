import { describe, expect, it } from "vitest";

import { limitUnit, limitsToApi, sizePreview } from "./sizeLimits.js";

const utm = { crs: "EPSG:32648", pixel_size: 10, proj4: "+proj=utm +zone=48 +units=m +no_defs" };
const png = { crs: null, pixel_size: 1 };

describe("limitUnit", () => {
  it("is metres when the image has them, else pixels", () => {
    expect(limitUnit(utm)).toBe("m");
    expect(limitUnit(png)).toBe("px");
  });
});

describe("limitsToApi", () => {
  it("turns a side length into an area in image pixels²", () => {
    expect(limitsToApi({ min: 50, max: 500 }, utm)).toEqual({ min_size: 25, max_size: 2500 });
    expect(limitsToApi({ min: 8, max: "" }, png)).toEqual({ min_size: 64, max_size: null });
  });
  it("sends nothing for empty or zero boxes", () => {
    expect(limitsToApi({ min: "", max: 0 }, utm)).toEqual({ min_size: null, max_size: null });
  });
});

describe("sizePreview", () => {
  it("gives a square and an equal-area circle per limit, in map units", () => {
    const [min, max] = sizePreview({ min: 50, max: 500 }, utm, 10);
    expect(min).toMatchObject({ kind: "min", side: 50, label: "min 50 m · 2500 m²" });
    expect(min.radius).toBeCloseTo(50 / Math.sqrt(Math.PI));
    expect(max).toMatchObject({ kind: "max", side: 500, label: "max 500 m · 250000 m²" });
  });
  it("works in pixels and skips unset limits", () => {
    expect(sizePreview({ min: "", max: 20 }, png, 1)).toEqual([
      { kind: "max", side: 20, radius: 20 / Math.sqrt(Math.PI), label: "max 20 px · 400 px²" },
    ]);
  });
});

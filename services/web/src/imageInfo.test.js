import { describe, expect, it } from "vitest";

import { pixelSizeText, tileCount } from "./imageInfo.js";

describe("pixelSizeText", () => {
  it("reads metres from a UTM image", () => {
    const image = { crs: "EPSG:32648", pixel_size: 10, proj4: "+proj=utm +zone=48 +units=m +no_defs" };
    expect(pixelSizeText(image)).toBe("10 m");
  });
  it("reads degrees from a lon/lat image", () => {
    const image = { crs: "EPSG:4326", pixel_size: 0.0000898, proj4: "+proj=longlat +datum=WGS84 +no_defs" };
    expect(pixelSizeText(image)).toBe("0.00009°");
  });
  it("is unknown for a PNG", () => {
    expect(pixelSizeText({ crs: null, pixel_size: 1 })).toBeNull();
  });
});

describe("tileCount", () => {
  it("is one tile when the image fits", () => {
    expect(tileCount(800, 600, 1024, 128)).toBe(1);
  });
  it("counts overlapping tiles on each side", () => {
    // 2000 px: tiles start every 896 px -> 3 across; 1000 px -> 1 down
    expect(tileCount(2000, 1000, 1024, 128)).toBe(3);
  });
  it("is unknown when overlap is not smaller than the tile", () => {
    expect(tileCount(2000, 2000, 256, 256)).toBeNull();
  });
});

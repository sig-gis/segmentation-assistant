import { describe, expect, it } from "vitest";

import { lengthText, metresPerPixel, pixelSizeText, sizeText } from "./imageInfo.js";

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

const utm = { crs: "EPSG:32648", pixel_size: 10, proj4: "+proj=utm +zone=48 +units=m +no_defs" };

describe("metresPerPixel", () => {
  it("is the pixel size for images in metres", () => {
    expect(metresPerPixel(utm)).toBe(10);
  });
  it("is null for degrees and PNGs", () => {
    expect(metresPerPixel({ crs: "EPSG:4326", pixel_size: 0.0001, proj4: "+proj=longlat" })).toBeNull();
    expect(metresPerPixel({ crs: null, pixel_size: 1 })).toBeNull();
  });
});

describe("sizeText", () => {
  it("gives side and area in km for a large square in metres", () => {
    expect(sizeText(1008, 1008, utm)).toBe("10.08 km, 101.6 km²");
  });
  it("uses m and m² for small sizes and shows both sides when not square", () => {
    expect(sizeText(5, 3, utm)).toBe("50 × 30 m, 1500 m²");
  });
  it("falls back to pixels", () => {
    expect(sizeText(1008, 1008, { crs: null })).toBe("1008 px, 1,016,064 px²");
  });
});

describe("lengthText", () => {
  it("gives a length in m or km, or px", () => {
    expect(lengthText(126, utm)).toBe("1.26 km");
    expect(lengthText(12, utm)).toBe("120 m");
    expect(lengthText(126, { crs: null })).toBe("126 px");
  });
});

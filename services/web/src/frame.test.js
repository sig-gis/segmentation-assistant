import { describe, expect, it } from "vitest";
import { collectionToMap, imageFrame, mapGeometry } from "./frame.js";

const png = { image_id: "p1", crs: null, width: 100, height: 50 };
const utm = {
  image_id: "g1",
  crs: "EPSG:32648",
  proj4: "+proj=utm +zone=48 +datum=WGS84 +units=m +no_defs",
};
const square = {
  type: "Polygon",
  coordinates: [[[1, 2], [11, 2], [11, 12], [1, 12], [1, 2]]],
};

describe("mapGeometry", () => {
  it("maps nested coordinates of any depth", () => {
    const multi = { type: "MultiPolygon", coordinates: [square.coordinates] };
    const doubled = mapGeometry(multi, ([x, y]) => [x * 2, y * 2]);
    expect(doubled.coordinates[0][0][1]).toEqual([22, 4]);
  });
});

describe("imageFrame for PNGs", () => {
  const frame = imageFrame(png);

  it("flips y between API rows (down) and map pixels (up)", () => {
    expect(frame.toMap(square).coordinates[0][0]).toEqual([1, 48]);
    expect(frame.pointToApi([1, 48])).toEqual([1, 2]);
  });

  it("round-trips geometry", () => {
    expect(frame.toApi(frame.toMap(square))).toEqual(square);
  });

  it("uses a pixel projection covering the image", () => {
    expect(frame.georeferenced).toBe(false);
    expect(frame.projection.getUnits()).toBe("pixels");
    expect(frame.extent).toEqual([0, 0, 100, 50]);
  });
});

describe("imageFrame for GeoTIFFs", () => {
  it("registers the CRS and passes geometry through", () => {
    const frame = imageFrame(utm);
    expect(frame.projection.getCode()).toBe("EPSG:32648");
    expect(frame.toMap(square)).toBe(square);
    expect(frame.pointToApi([500100, 1000000])).toEqual([500100, 1000000]);
  });

  it("refuses an unknown CRS with no definition", () => {
    expect(() => imageFrame({ crs: "EPSG:99999", proj4: null })).toThrow();
  });
});

describe("collectionToMap", () => {
  it("converts every feature", () => {
    const fc = { type: "FeatureCollection", features: [{ type: "Feature", geometry: square }] };
    expect(collectionToMap(fc, imageFrame(png)).features[0].geometry.coordinates[0][0])
      .toEqual([1, 48]);
  });
});

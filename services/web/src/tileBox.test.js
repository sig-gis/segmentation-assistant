import { describe, expect, it } from "vitest";

import { tileBox } from "./tileBox.js";

const extent = [0, 0, 10000, 8000]; // e.g. 1000 x 800 px at 10 m

describe("tileBox", () => {
  it("starts at the image centre, sized in map units", () => {
    expect(tileBox({ center: null, sizePx: 100, pixel: 10, extent })).toEqual([4500, 3500, 5500, 4500]);
  });

  it("stays inside the image when moved past an edge", () => {
    expect(tileBox({ center: [9900, -50], sizePx: 100, pixel: 10, extent }))
      .toEqual([9000, 0, 10000, 1000]);
  });

  it("is no larger than the image, as a sweep tile cannot be", () => {
    expect(tileBox({ center: null, sizePx: 2000, pixel: 10, extent })).toEqual(extent);
  });

  it("is null without a tile size or an extent", () => {
    expect(tileBox({ center: null, sizePx: "", pixel: 10, extent })).toBeNull();
    expect(tileBox({ center: null, sizePx: 100, pixel: 10, extent: null })).toBeNull();
  });
});

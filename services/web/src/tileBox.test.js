import { describe, expect, it } from "vitest";

import { tileBox } from "./tileBox.js";

const extent = [0, 0, 10000, 8000]; // e.g. 1000 x 800 px at 10 m

describe("tileBox", () => {
  it("starts at the image centre, sized in map units", () => {
    const box = tileBox({ center: null, sizePx: 100, overlapPx: 10, pixel: 10, extent });
    expect(box.outer).toEqual([4500, 3500, 5500, 4500]);
    expect(box.inner).toEqual([4600, 3600, 5400, 4400]);
  });

  it("stays inside the image when moved past an edge", () => {
    const box = tileBox({ center: [9900, -50], sizePx: 100, overlapPx: 10, pixel: 10, extent });
    expect(box.outer).toEqual([9000, 0, 10000, 1000]);
  });

  it("is no larger than the image, as a sweep tile cannot be", () => {
    const box = tileBox({ center: null, sizePx: 2000, overlapPx: 0, pixel: 10, extent });
    expect(box.outer).toEqual(extent);
  });

  it("has no inner box when the overlap fills the tile", () => {
    const box = tileBox({ center: null, sizePx: 100, overlapPx: 60, pixel: 10, extent });
    expect(box.inner).toBeNull();
  });

  it("is null without settings or an extent", () => {
    expect(tileBox({ center: null, sizePx: "", overlapPx: 10, pixel: 10, extent })).toBeNull();
    expect(tileBox({ center: null, sizePx: 100, overlapPx: 10, pixel: 10, extent: null })).toBeNull();
  });
});

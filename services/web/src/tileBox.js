// The one tile shown on the map to judge sweep tile size: the extent
// [minx, miny, maxx, maxy] (map units) of `sizePx` image pixels around
// `center` (the image centre when null), kept inside the image.

export function tileBox({ center, sizePx, pixel, extent }) {
  if (!extent || !sizePx || !pixel) return null;
  const [minx, miny, maxx, maxy] = extent;
  // a sweep tile is never larger than the image
  const width = Math.min(sizePx * pixel, maxx - minx);
  const height = Math.min(sizePx * pixel, maxy - miny);
  const [cx, cy] = center ?? [(minx + maxx) / 2, (miny + maxy) / 2];
  const x0 = Math.min(Math.max(cx - width / 2, minx), maxx - width);
  const y0 = Math.min(Math.max(cy - height / 2, miny), maxy - height);
  return [x0, y0, x0 + width, y0 + height];
}

// The one tile shown on the map to judge sweep tile size: a box of
// `sizePx` image pixels around `center` (map coordinates; the image centre
// when null), kept inside the image, and the inner box left once the
// overlap shared with neighbouring tiles (`overlapPx` on each side) is
// taken off. Extents are [minx, miny, maxx, maxy] in map units.

export function tileBox({ center, sizePx, overlapPx, pixel, extent }) {
  if (!extent || !sizePx || !pixel) return null;
  const [minx, miny, maxx, maxy] = extent;
  // a sweep tile is never larger than the image
  const width = Math.min(sizePx * pixel, maxx - minx);
  const height = Math.min(sizePx * pixel, maxy - miny);
  const [cx, cy] = center ?? [(minx + maxx) / 2, (miny + maxy) / 2];
  const x0 = Math.min(Math.max(cx - width / 2, minx), maxx - width);
  const y0 = Math.min(Math.max(cy - height / 2, miny), maxy - height);
  const outer = [x0, y0, x0 + width, y0 + height];
  const band = (overlapPx || 0) * pixel;
  const inner = 2 * band < width && 2 * band < height
    ? [outer[0] + band, outer[1] + band, outer[2] - band, outer[3] - band]
    : null;
  return { outer, inner };
}


// Plain-language facts about an image for the side panels.

// Size of one pixel on the ground, e.g. "10 m", from the image's own map
// units. null when unknown (PNGs have no georeferencing).
export function pixelSizeText(image) {
  const size = image?.pixel_size;
  if (!image?.crs || !size) return null;
  const proj = image.proj4 ?? "";
  const unit = proj.includes("+proj=longlat") ? "°"
    : /\+units=(us-)?ft\b/.test(proj) ? " ft"
    : proj.includes("+units=m") ? " m"
    : " map units";
  const digits = size >= 10 ? 0 : size >= 1 ? 1 : 6;
  return `${Number(size.toFixed(digits))}${unit}`;
}

// Ground size of one pixel in metres, or null when the image is not in
// metres (PNGs, degrees, feet).
export function metresPerPixel(image) {
  const size = image?.pixel_size;
  if (!image?.crs || !size || !(image.proj4 ?? "").includes("+units=m")) return null;
  return size;
}

const short = (n) => Number(n.toPrecision(4));

// "10.08 km, 101.6 km²" for a width x height in pixels; pixels when the image
// has no size in metres.
export function sizeText(width, height, image) {
  const m = metresPerPixel(image);
  const sides = (unit, scale) =>
    width === height ? `${short(width * scale)} ${unit}`
      : `${short(width * scale)} × ${short(height * scale)} ${unit}`;
  if (!m) {
    return `${sides("px", 1)}, ${(width * height).toLocaleString("en-US")} px²`;
  }
  const length = Math.max(width, height) * m;
  const area = width * height * m * m;
  const side = length >= 1000 ? sides("km", m / 1000) : sides("m", m);
  return `${side}, ${area >= 1e6 ? `${short(area / 1e6)} km²` : `${short(area)} m²`}`;
}

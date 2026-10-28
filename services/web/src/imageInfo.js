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

// How many tiles a sweep cuts the image into.
export function tileCount(width, height, tileSize, overlap) {
  if (!width || !height || !tileSize || overlap >= tileSize) return null;
  const along = (length) => (length <= tileSize ? 1 : Math.ceil((length - overlap) / (tileSize - overlap)));
  return along(width) * along(height);
}

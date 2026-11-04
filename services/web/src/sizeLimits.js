// Size limits for sweeps and searches. People enter the side of a square
// (the smallest and largest object to keep), in metres when the image has
// them and in pixels otherwise; the API takes areas in image pixels².
import { areaText, lengthText, metresPerPixel } from "./imageInfo.js";

export function limitUnit(image) {
  return metresPerPixel(image) ? "m" : "px";
}

// side length in the image's pixels, or null for an empty or zero box
function sidePx(value, image) {
  const side = Number(value);
  if (!value || !side) return null;
  return side / (metresPerPixel(image) ?? 1);
}

export function limitsToApi({ min, max }, image) {
  const area = (value) => {
    const side = sidePx(value, image);
    return side == null ? null : side * side;
  };
  return { min_size: area(min), max_size: area(max) };
}

// What to draw for each limit that is set: a square of that side and a
// circle of the same area, in map units (`pixel` map units per image pixel).
export function sizePreview(limits, image, pixel) {
  return ["min", "max"].flatMap((kind) => {
    const side = sidePx(limits[kind], image);
    if (side == null) return [];
    const mapSide = side * pixel;
    return [{
      kind,
      side: mapSide,
      radius: mapSide / Math.sqrt(Math.PI),
      label: `${kind} ${lengthText(side, image)} · ${areaText(side * side, image)}`,
    }];
  });
}

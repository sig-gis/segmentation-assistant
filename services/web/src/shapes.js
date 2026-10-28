// Erase and Add: cut a drawn shape out of the outline, or merge it in.
import polygonClipping from "polygon-clipping";

function coordinatesOf(geometry) {
  return geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
}

// null when nothing would be left (the caller explains instead of saving an
// empty outline)
export function combine(geometry, shape, operation) {
  const apply = operation === "erase" ? polygonClipping.difference : polygonClipping.union;
  const result = apply(coordinatesOf(geometry), coordinatesOf(shape));
  return result.length ? { type: "MultiPolygon", coordinates: result } : null;
}

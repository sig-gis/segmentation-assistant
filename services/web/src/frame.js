// How an image's coordinates map onto the OpenLayers map.
//
// GeoTIFF: the map uses the image's own CRS (e.g. UTM), registered from the
// proj4 text the API sends, so API geometry and map geometry are identical.
// PNG: no CRS. The map uses a pixel "projection" whose y axis points up,
// while the API's pixel rows point down, so y is flipped both ways.

import proj4 from "proj4";
import Projection from "ol/proj/Projection.js";
import { get as getProjection } from "ol/proj.js";
import { register } from "ol/proj/proj4.js";

export function mapCoordinates(coordinates, fn) {
  if (typeof coordinates[0] === "number") return fn(coordinates);
  return coordinates.map((c) => mapCoordinates(c, fn));
}

export function mapGeometry(geometry, fn) {
  if (!geometry) return geometry;
  return { ...geometry, coordinates: mapCoordinates(geometry.coordinates, fn) };
}

export function imageFrame(image) {
  if (image.crs) {
    if (!getProjection(image.crs)) {
      if (!image.proj4) throw new Error(`no projection definition for ${image.crs}`);
      proj4.defs(image.crs, image.proj4);
      register(proj4);
    }
    const identity = (g) => g;
    return {
      georeferenced: true,
      projection: getProjection(image.crs),
      toMap: identity,
      toApi: identity,
      pointToApi: (xy) => xy,
    };
  }
  const { width, height } = image;
  const flip = ([x, y, ...rest]) => [x, height - y, ...rest];
  return {
    georeferenced: false,
    extent: [0, 0, width, height],
    projection: new Projection({
      code: `pixels-${image.image_id}`,
      units: "pixels",
      extent: [0, 0, width, height],
    }),
    toMap: (g) => mapGeometry(g, flip),
    toApi: (g) => mapGeometry(g, flip),
    pointToApi: flip,
  };
}

// FeatureCollection from the API -> same collection in map coordinates.
export function collectionToMap(collection, frame) {
  return {
    ...collection,
    features: collection.features.map((f) => ({ ...f, geometry: frame.toMap(f.geometry) })),
  };
}

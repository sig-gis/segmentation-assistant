// The outline being worked on ("draft"): one object at a time.
//
// A draft comes from the model (box or clicks), from a saved outline opened
// for editing, or from a suggestion. Edits are kept locally with an undo
// history and only written to the API on Save. Geometry is a GeoJSON
// MultiPolygon in map coordinates.

export function newDraft({ annotationId, geometry, kind, label, box = null, points = [],
                           cleanup = { fill_holes: false, smoothing: "off" }, boxExtent = null,
                           replaces = null }) {
  return {
    annotationId,
    kind, // "model" (new, unsaved), "saved" (an accepted outline), "suggestion"
    label,
    geometry,
    history: [],
    dirty: false,
    selectedVertex: null,
    rangeEnd: null, // second corner of a Shift+click selection, for Straighten
    flipSide: false,
    selectedHole: null, // [polygon, ring] of a selected hole
    box, // the box prompt in API coordinates, reused by refinement clicks
    boxExtent, // the same box in map coordinates, for drawing it
    points, // refinement clicks so far: [{x, y, label, mapCoordinate}]
    cleanup,
    replaces, // a saved outline this one replaces when saved (after refining it)
    simplifyBase: null, // the shape before simplifying, so levels stay reversible
    simplifyLevel: 0,
  };
}

export function edit(draft, geometry) {
  return {
    ...draft,
    history: [...draft.history, draft.geometry],
    geometry,
    dirty: true,
    simplifyBase: null,
    simplifyLevel: 0,
    ...NO_SELECTION,
  };
}

// Simplify to a level (0 = off), always from the shape as it was before any
// simplifying, so going from 4 px back to 1 px restores the corners.
export function simplifyTo(draft, level, tolerance) {
  const base = draft.simplifyBase ?? draft.geometry;
  const geometry = level ? simplify(base, tolerance) : base;
  return { ...edit(draft, geometry), simplifyBase: base, simplifyLevel: level };
}

export const NO_SELECTION = { selectedVertex: null, rangeEnd: null, flipSide: false,
                              selectedHole: null };

export function undo(draft) {
  if (!draft.history.length) return draft;
  const history = draft.history.slice(0, -1);
  return {
    ...draft,
    geometry: draft.history[draft.history.length - 1],
    history,
    dirty: history.length > 0,
    simplifyBase: null,
    simplifyLevel: 0,
    ...NO_SELECTION,
  };
}

function asMulti(geometry) {
  return geometry.type === "Polygon"
    ? { type: "MultiPolygon", coordinates: [geometry.coordinates] }
    : geometry;
}

// every corner, with its address [polygon, ring, index]; the closing
// duplicate of each ring is skipped
export function vertices(geometry) {
  const out = [];
  asMulti(geometry).coordinates.forEach((polygon, p) =>
    polygon.forEach((ring, r) =>
      ring.slice(0, -1).forEach((coordinate, i) => out.push({ ref: [p, r, i], coordinate }))
    )
  );
  return out;
}

export function nearestVertex(geometry, [x, y], tolerance) {
  let best = null;
  for (const v of vertices(geometry)) {
    const d = Math.hypot(v.coordinate[0] - x, v.coordinate[1] - y);
    if (d <= tolerance && (!best || d < best.distance)) best = { ...v, distance: d };
  }
  return best?.ref ?? null;
}

export function vertexAt(geometry, [p, r, i]) {
  return asMulti(geometry).coordinates[p]?.[r]?.[i] ?? null;
}

// Remove one corner. Returns null when the ring would get fewer than three
// corners (the caller says why instead of breaking the shape).
export function deleteVertex(geometry, [p, r, i]) {
  const multi = asMulti(geometry);
  const ring = multi.coordinates[p][r].slice(0, -1);
  if (ring.length <= 3) return null;
  ring.splice(i, 1);
  const closed = [...ring, ring[0]];
  const coordinates = multi.coordinates.map((polygon, pi) =>
    pi !== p ? polygon : polygon.map((rg, ri) => (ri === r ? closed : rg))
  );
  return { type: "MultiPolygon", coordinates };
}

export function holeCount(geometry) {
  return asMulti(geometry).coordinates.reduce((n, polygon) => n + polygon.length - 1, 0);
}

export function cornerCount(geometry) {
  return vertices(geometry).length;
}

// [xmin, ymin, xmax, ymax] in API coordinates from a drawn box (map coords)
export function boxToApi(extent, pointToApi) {
  const [x0, y0, x1, y1] = extent;
  const corners = [[x0, y0], [x1, y1]].map(pointToApi);
  const xs = corners.map((c) => c[0]);
  const ys = corners.map((c) => c[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

// [minx, miny, maxx, maxy] of a geometry
export function extentOf(geometry) {
  const xs = [];
  const ys = [];
  for (const { coordinate } of vertices(geometry)) {
    xs.push(coordinate[0]);
    ys.push(coordinate[1]);
  }
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

// --- straighten: remove the corners between two selected corners ---------

// Indexes strictly between corners a and b of one ring, on the side that
// bulges least from the straight line a-b (the bump or lobe to flatten), or
// the other side when `flip`. null if a and b are not on the same ring.
export function cornersBetween(geometry, a, b, flip = false) {
  if (!a || !b || a[0] !== b[0] || a[1] !== b[1] || a[2] === b[2]) return null;
  const ring = asMulti(geometry).coordinates[a[0]][a[1]];
  const n = ring.length - 1;
  const [i, j] = a[2] < b[2] ? [a[2], b[2]] : [b[2], a[2]];
  const inside = [];
  for (let k = i + 1; k < j; k++) inside.push(k);
  const outside = [];
  for (let k = j + 1; k < n + i; k++) outside.push(k % n);
  // area enclosed by each side's path and the straight line between a and b
  const bulge = (path) => {
    const points = [ring[i], ...path.map((k) => ring[k]), ring[j]];
    let twice = 0;
    for (let k = 0; k < points.length; k++) {
      const [x0, y0] = points[k];
      const [x1, y1] = points[(k + 1) % points.length];
      twice += x0 * y1 - x1 * y0;
    }
    return Math.abs(twice) / 2;
  };
  const smaller = bulge(inside) <= bulge([...outside].reverse()) ? inside : outside;
  const larger = smaller === inside ? outside : inside;
  return flip ? larger : smaller;
}

function removeCorners(geometry, [p, r], indexes) {
  const multi = asMulti(geometry);
  const drop = new Set(indexes);
  const ring = multi.coordinates[p][r].slice(0, -1).filter((_, k) => !drop.has(k));
  if (ring.length < 3) return null;
  return replaceRing(multi, [p, r], [...ring, ring[0]]);
}

function replaceRing(multi, [p, r], ring) {
  return {
    type: "MultiPolygon",
    coordinates: multi.coordinates.map((polygon, pi) =>
      pi !== p ? polygon : polygon.map((rg, ri) => (ri === r ? ring : rg))
    ),
  };
}

export function straighten(geometry, a, b, flip = false) {
  const indexes = cornersBetween(geometry, a, b, flip);
  if (!indexes) return null;
  return removeCorners(geometry, [a[0], a[1]], indexes);
}

// --- simplify: drop corners within `tolerance` of a straight line ---------

function distanceToSegment([px, py], [ax, ay], [bx, by]) {
  const dx = bx - ax;
  const dy = by - ay;
  const length2 = dx * dx + dy * dy;
  const t = length2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length2)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

// Douglas-Peucker on an open path (keeps both ends)
function simplifyPath(points, tolerance) {
  if (points.length < 3) return points;
  let worst = 0;
  let index = 0;
  for (let k = 1; k < points.length - 1; k++) {
    const d = distanceToSegment(points[k], points[0], points[points.length - 1]);
    if (d > worst) {
      worst = d;
      index = k;
    }
  }
  if (worst <= tolerance) return [points[0], points[points.length - 1]];
  const left = simplifyPath(points.slice(0, index + 1), tolerance);
  const right = simplifyPath(points.slice(index), tolerance);
  return [...left.slice(0, -1), ...right];
}

function simplifyRing(ring, tolerance) {
  const open = ring.slice(0, -1);
  // split at the corner farthest from the first, so both halves are open paths
  let far = 1;
  for (let k = 1; k < open.length; k++) {
    if (Math.hypot(open[k][0] - open[0][0], open[k][1] - open[0][1]) >
        Math.hypot(open[far][0] - open[0][0], open[far][1] - open[0][1])) far = k;
  }
  const first = simplifyPath(open.slice(0, far + 1), tolerance);
  const second = simplifyPath([...open.slice(far), open[0]], tolerance);
  const result = [...first.slice(0, -1), ...second.slice(0, -1)];
  return result.length >= 3 ? [...result, result[0]] : ring;
}

export function simplify(geometry, tolerance) {
  const multi = asMulti(geometry);
  return {
    type: "MultiPolygon",
    coordinates: multi.coordinates.map((polygon) => polygon.map((ring) => simplifyRing(ring, tolerance))),
  };
}

// --- holes ---------------------------------------------------------------

function insideRing([x, y], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// [polygon, ring] of the hole containing the point, or null
export function holeAt(geometry, point) {
  const polygons = asMulti(geometry).coordinates;
  for (let p = 0; p < polygons.length; p++) {
    for (let r = 1; r < polygons[p].length; r++) {
      if (insideRing(point, polygons[p][r])) return [p, r];
    }
  }
  return null;
}

export function removeHole(geometry, [p, r]) {
  const multi = asMulti(geometry);
  return {
    type: "MultiPolygon",
    coordinates: multi.coordinates.map((polygon, pi) =>
      pi !== p ? polygon : polygon.filter((_, ri) => ri !== r)
    ),
  };
}

export function ringAt(geometry, [p, r]) {
  return asMulti(geometry).coordinates[p]?.[r] ?? null;
}

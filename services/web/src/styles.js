// Map styles.
//   saved outlines   colored by label
//   suggestions      dashed yellow (unreviewed model output), or dashed in
//                    their sweep's color; thicker when their sweep is selected
//   draft            the outline being edited: blue edges, white corner dots
//   areas            dashed rectangles colored by review status
//   tiles            dashed white on black (no outline state uses it), with size
//   tile box         solid white edge on black, size label
import MultiPoint from "ol/geom/MultiPoint.js";
import Point from "ol/geom/Point.js";
import Polygon from "ol/geom/Polygon.js";
import { Circle, Fill, Stroke, Style, Text } from "ol/style.js";

import { labelColor } from "./layers.js";
import { vertices } from "./draft.js";

const cache = new Map();

function labelText(text) {
  return new Text({
    text,
    font: "600 12px system-ui, sans-serif",
    fill: new Fill({ color: "#fff" }),
    stroke: new Stroke({ color: "#000", width: 3 }),
    overflow: true,
  });
}

export function savedStyle(feature, showLabels, selectedSweep = null) {
  const label = feature.get("label");
  const bold = Boolean(selectedSweep) && feature.get("job_id") === selectedSweep;
  const key = `saved:${label}:${showLabels}:${bold}`;
  if (!cache.has(key)) {
    const color = labelColor(label);
    cache.set(key, new Style({
      stroke: new Stroke({ color, width: bold ? 4 : 2 }),
      fill: new Fill({ color: `${color}22` }),
      text: showLabels ? labelText(label) : undefined,
    }));
  }
  return cache.get(key);
}

const SUGGESTION_COLOR = "#f5b700";

// sweep: { colorOf(jobId) -> color, selected: jobId or null }
export function suggestionStyle(feature, selectedId, sweep = {}) {
  const jobId = feature.get("job_id");
  const color = (jobId && sweep.colorOf?.(jobId)) || SUGGESTION_COLOR;
  if (feature.getId() !== selectedId) {
    const bold = Boolean(jobId) && jobId === sweep.selected;
    const key = `suggestion:${color}:${bold}`;
    if (!cache.has(key)) {
      cache.set(key, new Style({
        stroke: new Stroke({ color, width: bold ? 3.5 : 2, lineDash: [6, 4] }),
        fill: new Fill({ color: `${color}${bold ? "33" : "14"}` }),
      }));
    }
    return cache.get(key);
  }
  return new Style({
    stroke: new Stroke({ color, width: 4 }),
    fill: new Fill({ color: `${color}33` }),
    text: labelText(
      [feature.get("label"), feature.get("score") == null ? null : feature.get("score").toFixed(2)]
        .filter(Boolean).join(" · ")
    ),
  });
}

const DRAFT_EDGE = new Style({
  stroke: new Stroke({ color: "#2f7dff", width: 2.5 }),
  fill: new Fill({ color: "#2f7dff1f" }),
});
const CORNER = new Circle({
  radius: 5,
  fill: new Fill({ color: "#fff" }),
  stroke: new Stroke({ color: "#2f7dff", width: 2 }),
});
const SELECTED_CORNER = new Circle({
  radius: 7,
  fill: new Fill({ color: "#e5484d" }),
  stroke: new Stroke({ color: "#fff", width: 2 }),
});

const REMOVING_CORNER = new Circle({
  radius: 5,
  fill: new Fill({ color: "#f08c00" }),
  stroke: new Stroke({ color: "#fff", width: 1.5 }),
});
const HOLE_STROKE = new Stroke({ color: "#e5484d", width: 3 });
const HOLE_FILL = new Fill({ color: "#e5484d40" });

// Edges and corners are drawn separately so corners read as handles.
// highlight: {selected, rangeEnd} corner coordinates, `removing` = corners
// Straighten would remove (orange), `hole` = ring of a selected hole.
// With showCorners off only the outline is drawn, to judge it against the image.
export function draftStyles(highlight = {}, showCorners = true) {
  return (feature) => {
    const geometry = feature.get("json");
    if (!showCorners) return [DRAFT_EDGE];
    const styles = [
      DRAFT_EDGE,
      new Style({
        image: CORNER,
        geometry: () => new MultiPoint(vertices(geometry).map((v) => v.coordinate)),
      }),
    ];
    if (highlight.removing?.length) {
      styles.push(new Style({ image: REMOVING_CORNER, geometry: new MultiPoint(highlight.removing) }));
    }
    for (const corner of [highlight.selected, highlight.rangeEnd]) {
      if (corner) styles.push(new Style({ image: SELECTED_CORNER, geometry: new Point(corner) }));
    }
    if (highlight.hole) {
      styles.push(new Style({ stroke: HOLE_STROKE, fill: HOLE_FILL,
                              geometry: new Polygon([highlight.hole]) }));
    }
    return styles;
  };
}

const POINT_STYLES = {
  true: new Style({
    image: new Circle({ radius: 6, fill: new Fill({ color: "#2bb24c" }),
      stroke: new Stroke({ color: "#fff", width: 2 }) }),
  }),
  false: new Style({
    image: new Circle({ radius: 6, fill: new Fill({ color: "#e5484d" }),
      stroke: new Stroke({ color: "#fff", width: 2 }) }),
  }),
};

// refinement clicks: green = include, red = exclude
export function pointStyle(feature) {
  return POINT_STYLES[Boolean(feature.get("include"))];
}

export const BOX_STYLE = new Style({
  stroke: new Stroke({ color: "#2f7dff", width: 1.5, lineDash: [4, 4] }),
});

export function tileStyle(feature) {
  return [
    new Style({ stroke: new Stroke({ color: "#000", width: 3 }) }),
    new Style({
      stroke: new Stroke({ color: "#fff", width: 1.5, lineDash: [10, 6] }),
      text: labelText(feature.get("size")),
    }),
  ];
}

const TILE_EDGE = [
  new Style({ stroke: new Stroke({ color: "#000", width: 4 }) }),
  new Style({ stroke: new Stroke({ color: "#fff", width: 2.5 }) }),
];

// parts: "edge" (the tile outline) and "label"
export function tileBoxStyle(feature) {
  return feature.get("part") === "edge"
    ? TILE_EDGE
    : new Style({ text: labelText(feature.get("text")) });
}

export const AREA_COLORS = {
  draft: "#9aa4b2",
  submitted: "#2f7dff",
  approved: "#2bb24c",
  returned: "#e5484d",
};

export function areaStyle(feature) {
  const color = AREA_COLORS[feature.get("status")] ?? "#9aa4b2";
  return new Style({
    stroke: new Stroke({ color, width: 2, lineDash: [8, 6] }),
    text: new Text({
      text: `${feature.get("label")} area (${feature.get("status")})`,
      font: "600 12px system-ui, sans-serif",
      fill: new Fill({ color: "#fff" }),
      stroke: new Stroke({ color: "#000", width: 3 }),
      placement: "line",
      overflow: true,
    }),
  });
}

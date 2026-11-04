// The OpenLayers map: the selected image, outlines, and the tools.
//
// The map object is created once; layers and view are swapped when the image
// changes. Saved outlines and suggestions arrive in API coordinates and are
// converted with frame.toMap; the draft is already in map coordinates.
//
// Tools (`mode`, used while no draft is open):
//   pan     drag to move the map; click a saved outline or suggestion to open it
//   box     drag a rectangle; SAM outlines the object inside it
//   click   click an object; SAM outlines it
//   draw    draw an outline by hand (double-click to finish)
//   area    drag a rectangle to mark a labeled area
// The sweep tile box (`tileBox`) can be dragged in pan mode; `onTileMove`
// gets its new centre and the parent sends back the box, kept in the image.
// While a draft is open (`draftTool`):
//   corners drag a corner, click an edge to add one, click a corner to
//           select it, Shift+click a second corner to select the stretch
//           between them, click inside a hole to select it, Alt+click
//           removes a corner
//   refine  click to include (Alt+click to exclude) and let SAM redo it
//   erase   draw a shape; it is cut out of the outline
//   add     draw a shape; it is merged into the outline
import { useEffect, useRef, useState } from "react";
import Collection from "ol/Collection.js";
import Feature from "ol/Feature.js";
import Map from "ol/Map.js";
import View from "ol/View.js";
import GeoJSON from "ol/format/GeoJSON.js";
import CircleGeometry from "ol/geom/Circle.js";
import LineString from "ol/geom/LineString.js";
import Point from "ol/geom/Point.js";
import { fromExtent } from "ol/geom/Polygon.js";
import Draw, { createBox } from "ol/interaction/Draw.js";
import Modify from "ol/interaction/Modify.js";
import Translate from "ol/interaction/Translate.js";
import ImageLayer from "ol/layer/Image.js";
import TileLayer from "ol/layer/WebGLTile.js";
import VectorLayer from "ol/layer/Vector.js";
import GeoTIFF from "ol/source/GeoTIFF.js";
import ImageStatic from "ol/source/ImageStatic.js";
import VectorSource from "ol/source/Vector.js";
import { defaults as defaultControls, ScaleLine } from "ol/control.js";
import { buffer, getCenter, getHeight, getWidth } from "ol/extent.js";
import "ol/ol.css";

import { api } from "../api.js";
import { holeAt, nearestVertex } from "../draft.js";
import {
  BOX_STYLE, areaStyle, draftStyles, pointStyle, savedStyle, sizePreviewStyle, suggestionStyle,
  tileBoxStyle, tileStyle,
} from "../styles.js";

const format = new GeoJSON();
const CORNER_PICK_PIXELS = 9;

function featureFrom(json, id, properties = {}) {
  const feature = format.readFeature({ type: "Feature", geometry: json, properties });
  if (id) feature.setId(id);
  return feature;
}

export default function MapView({
  image,
  frame,
  saved = [],
  suggestions = [],
  showLabels = true,
  areas = [],
  tiles = [],
  tileBox = null,
  sizePreview = [],
  draft = null,
  draftTool = "corners",
  highlight = {},
  showCorners = true,
  mode = "pan",
  selectedSuggestionId = null,
  sweepColorOf = null,
  selectedSweep = null,
  focus = null,
  onPick,
  onBox,
  onPoint,
  onDraw,
  onDrawArea,
  onDraftChange,
  onVertexSelect,
  onHoleSelect,
  onDraftShape,
  onTileMove,
}) {
  const target = useRef(null);
  const mapRef = useRef(null);
  const sources = useRef({
    saved: new VectorSource(),
    suggestions: new VectorSource(),
    areas: new VectorSource(),
    tiles: new VectorSource(),
    tileBox: new VectorSource(),
    sizePreview: new VectorSource(),
    draft: new VectorSource(),
    marks: new VectorSource(),
  });
  const layers = useRef({});
  // which image the current view belongs to, and which zoom request was done
  const [viewImageId, setViewImageId] = useState(null);
  const appliedFocus = useRef(null);
  // latest props for map listeners
  const live = useRef({});
  live.current = { mode, draft, draftTool, onPick, onPoint, onVertexSelect, onHoleSelect, frame };

  // create the map once
  useEffect(() => {
    const s = sources.current;
    layers.current = {
      tiles: new VectorLayer({ source: s.tiles, style: tileStyle, zIndex: 4, declutter: true }),
      areas: new VectorLayer({ source: s.areas, style: areaStyle, zIndex: 5 }),
      tileBox: new VectorLayer({ source: s.tileBox, style: tileBoxStyle, zIndex: 9 }),
      sizePreview: new VectorLayer({ source: s.sizePreview, style: sizePreviewStyle, zIndex: 25 }),
      saved: new VectorLayer({ source: s.saved, zIndex: 10 }),
      suggestions: new VectorLayer({ source: s.suggestions, zIndex: 11 }),
      draft: new VectorLayer({ source: s.draft, zIndex: 20 }),
      marks: new VectorLayer({ source: s.marks, zIndex: 30 }),
    };
    const map = new Map({
      target: target.current,
      layers: Object.values(layers.current),
      controls: defaultControls().extend([new ScaleLine()]),
    });
    map.on("singleclick", (event) => {
      const { mode, draft, draftTool, onPick, onPoint, onVertexSelect, onHoleSelect } = live.current;
      const exclude = event.originalEvent.altKey;
      if (draft) {
        if (draftTool === "refine") {
          onPoint?.(event.coordinate, { exclude });
        } else if (draftTool === "corners" && !exclude) { // Alt+click: Modify removes the corner
          const tolerance = map.getView().getResolution() * CORNER_PICK_PIXELS;
          const corner = nearestVertex(draft.geometry, event.coordinate, tolerance);
          if (corner) {
            onVertexSelect?.(corner, { extend: event.originalEvent.shiftKey });
          } else {
            onHoleSelect?.(holeAt(draft.geometry, event.coordinate));
          }
        }
        return;
      }
      if (mode === "click") {
        onPoint?.(event.coordinate, { exclude });
      } else if (mode === "pan") {
        const hit = map.forEachFeatureAtPixel(event.pixel, (f, layer) => ({ f, layer }), {
          layerFilter: (l) => l === layers.current.saved || l === layers.current.suggestions,
          hitTolerance: 3,
        });
        if (hit) onPick?.(hit.f.getId(), hit.layer === layers.current.saved ? "saved" : "suggestion");
      }
    });
    mapRef.current = map;
    return () => map.setTarget(undefined);
  }, []);

  // image layer + view
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !image || !frame) return;
    let layer;
    let cancelled = false;
    if (frame.georeferenced) {
      const source = new GeoTIFF({
        sources: [{ url: api.displayUrl(image.image_id), nodata: 0 }],
        projection: frame.projection,
        interpolate: false,
      });
      layer = new TileLayer({ source });
      source.getView().then((viewOptions) => {
        if (cancelled) return;
        const view = new View({ projection: frame.projection });
        map.setView(view);
        view.fit(viewOptions.extent);
        setViewImageId(image.image_id);
      });
    } else {
      layer = new ImageLayer({
        source: new ImageStatic({
          url: api.displayUrl(image.image_id),
          imageExtent: frame.extent,
          projection: frame.projection,
          interpolate: false,
        }),
      });
      const view = new View({ projection: frame.projection });
      map.setView(view);
      view.fit(frame.extent);
      setViewImageId(image.image_id);
    }
    map.getLayers().insertAt(0, layer);
    return () => {
      cancelled = true;
      map.removeLayer(layer);
    };
  }, [image, frame]);

  // saved outlines and suggestions
  useEffect(() => {
    if (!frame) return;
    const fill = (source, features) => {
      source.clear();
      source.addFeatures(
        features.map((f) => featureFrom(frame.toMap(f.geometry), f.id, f.properties))
      );
    };
    fill(sources.current.saved, saved);
    fill(sources.current.suggestions, suggestions);
  }, [saved, suggestions, frame]);

  useEffect(() => {
    layers.current.saved.setStyle((f) => savedStyle(f, showLabels, selectedSweep));
  }, [showLabels, selectedSweep]);

  useEffect(() => {
    const sweep = { colorOf: sweepColorOf, selected: selectedSweep };
    layers.current.suggestions.setStyle((f) => suggestionStyle(f, selectedSuggestionId, sweep));
  }, [selectedSuggestionId, sweepColorOf, selectedSweep]);

  // labeled areas
  useEffect(() => {
    const source = sources.current.areas;
    source.clear();
    if (!frame) return;
    source.addFeatures(
      areas.map((a) => featureFrom(frame.toMap(a.geometry), a.area_id,
                                   { label: a.label, status: a.status }))
    );
  }, [areas, frame]);

  // sweep tiles: [{ geometry (API coordinates), size (label text) }]
  useEffect(() => {
    const source = sources.current.tiles;
    source.clear();
    if (!frame) return;
    source.addFeatures(tiles.map((t) => featureFrom(frame.toMap(t.geometry), null, { size: t.size })));
  }, [tiles, frame]);

  // the sweep tile box: { extent, label }; in pan mode it can be dragged by
  // its edge or label (inside it, dragging still pans the map)
  useEffect(() => {
    const map = mapRef.current;
    const source = sources.current.tileBox;
    source.clear();
    if (!map || !tileBox) return;
    const edge = new LineString(fromExtent(tileBox.extent).getCoordinates()[0]);
    const parts = [
      new Feature({ geometry: edge, part: "edge" }),
      new Feature({ geometry: new Point(getCenter(tileBox.extent)), part: "label",
                    text: tileBox.label }),
    ];
    source.addFeatures(parts);
    if (mode !== "pan" || draft) return;
    const translate = new Translate({ features: new Collection(parts), hitTolerance: 6 });
    translate.on("translateend", () => onTileMove?.(getCenter(edge.getExtent())));
    map.addInteraction(translate);
    return () => map.removeInteraction(translate);
  }, [tileBox, mode, draft, onTileMove]);

  // size limits at true scale, at the centre of the view (kept there as the
  // map moves): [{ kind, side, radius (map units), label }]
  useEffect(() => {
    const map = mapRef.current;
    const source = sources.current.sizePreview;
    source.clear();
    if (!map || !sizePreview.length) return;
    const draw = () => {
      const center = map.getView()?.getCenter();
      source.clear();
      if (!center) return;
      const [cx, cy] = center;
      source.addFeatures(sizePreview.flatMap(({ kind, side, radius, label }) => {
        const half = side / 2;
        return [
          new Feature({ geometry: fromExtent([cx - half, cy - half, cx + half, cy + half]), kind }),
          new Feature({ geometry: new CircleGeometry(center, radius), kind }),
          new Feature({ geometry: new Point([cx, cy + half]), kind, part: "label", label }),
        ];
      }));
    };
    draw();
    map.on("moveend", draw);
    return () => map.un("moveend", draw);
  }, [sizePreview, viewImageId]);

  // the draft, its refinement clicks and its box
  useEffect(() => {
    const s = sources.current;
    s.draft.clear();
    s.marks.clear();
    if (!draft) return;
    const feature = featureFrom(draft.geometry);
    feature.set("json", draft.geometry);
    s.draft.addFeature(feature);
    layers.current.draft.setStyle(draftStyles(highlight, showCorners));
    s.marks.addFeatures(
      draft.points.map((p) => new Feature({ geometry: new Point(p.mapCoordinate),
                                            include: p.label === 1 }))
    );
    if (draft.boxExtent) {
      const box = new Feature({ geometry: fromExtent(draft.boxExtent) });
      box.setStyle(BOX_STYLE);
      s.marks.addFeature(box);
    }
  }, [draft, highlight, showCorners]);

  // erase / add: draw a shape to cut out of, or merge into, the draft
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !draft || (draftTool !== "erase" && draftTool !== "add")) return;
    const draw = new Draw({ type: "Polygon" });
    draw.on("drawend", (event) => {
      onDraftShape?.(format.writeGeometryObject(event.feature.getGeometry()), draftTool);
    });
    map.addInteraction(draw);
    return () => map.removeInteraction(draw);
  }, [draft, draftTool, onDraftShape]);

  // corner editing of the draft
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !draft || draftTool !== "corners") return;
    const feature = sources.current.draft.getFeatures()[0];
    if (!feature) return;
    const modify = new Modify({ features: new Collection([feature]), pixelTolerance: 8 });
    modify.on("modifyend", () => {
      onDraftChange?.(format.writeGeometryObject(feature.getGeometry()));
    });
    map.addInteraction(modify);
    return () => map.removeInteraction(modify);
  }, [draft, draftTool, onDraftChange]);

  // drawing tools (only while no draft is open)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !frame || draft) return;
    let draw = null;
    if (mode === "box" || mode === "area") {
      draw = new Draw({ type: "Circle", geometryFunction: createBox(), freehand: true });
      draw.on("drawend", (event) => {
        const geometry = event.feature.getGeometry();
        if (mode === "box") onBox?.(geometry.getExtent());
        else onDrawArea?.(frame.toApi(format.writeGeometryObject(geometry)));
      });
    } else if (mode === "draw") {
      draw = new Draw({ type: "Polygon" });
      draw.on("drawend", (event) => {
        onDraw?.(frame.toApi(format.writeGeometryObject(event.feature.getGeometry())));
      });
    }
    if (!draw) return;
    map.addInteraction(draw);
    return () => map.removeInteraction(draw);
  }, [mode, frame, draft, onBox, onDraw, onDrawArea]);

  // zoom to an outline or area on request: each request once, only on the
  // image it was made for, once that image's view and features are loaded
  // (re-applying old requests on every reload sent the view off the image)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focus || appliedFocus.current === focus.nonce) return;
    if (focus.imageId !== image?.image_id || viewImageId !== image?.image_id) return;
    const source = sources.current[focus.layer ?? "saved"] ?? sources.current.saved;
    const feature = focus.id ? source.getFeatureById(focus.id) : null;
    const extent = focus.extent ?? feature?.getGeometry().getExtent();
    if (!extent) return; // the feature may not be loaded yet
    const size = Math.max(getWidth(extent), getHeight(extent)) || 1;
    map.getView().fit(buffer(extent, size * 0.6), { duration: 250 });
    appliedFocus.current = focus.nonce;
  }, [focus, saved, suggestions, areas, image, viewImageId]);

  const cursor = draft ? (draftTool === "corners" ? "default" : "crosshair")
    : ["box", "click", "draw", "area"].includes(mode) ? "crosshair" : "default";
  return <div ref={target} className="map" style={{ cursor }} />;
}

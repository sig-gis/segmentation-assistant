// Top-level app: image list, annotations and draft state, map tool modes
// (1-5), side tabs, keyboard shortcuts, and panel settings kept in
// localStorage (visibility, widths, folds, theme, sweep:<imageId>).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api.js";
import {
  NO_SELECTION, boxToApi, cornerCount, cornersBetween, deleteVertex, edit, extentOf, newDraft,
  removeHole, ringAt, simplifyTo, straighten, undo, vertexAt,
} from "./draft.js";
import { combine } from "./shapes.js";
import { imageFrame } from "./frame.js";
import { lengthText, sizeText } from "./imageInfo.js";
import { tileBox } from "./tileBox.js";
import { defaultVisibility, toggleLabel, visibleFeatures } from "./layers.js";
import { nextId, reviewQueue, sweepsIn } from "./review.js";
import AreasPanel from "./components/AreasPanel.jsx";
import CompletePanel from "./components/CompletePanel.jsx";
import Fold from "./components/Fold.jsx";
import HelpDialog from "./components/HelpDialog.jsx";
import ImageList from "./components/ImageList.jsx";
import LabelsPanel from "./components/LabelsPanel.jsx";
import LayersPanel from "./components/LayersPanel.jsx";
import MapView from "./components/MapView.jsx";
import OutlinePanel from "./components/OutlinePanel.jsx";
import Splitter from "./components/Splitter.jsx";
import SuggestionsPanel from "./components/SuggestionsPanel.jsx";
import SweepPanel from "./components/SweepPanel.jsx";
import TablePanel from "./components/TablePanel.jsx";

const MODE_KEYS = { 1: "pan", 2: "box", 3: "click", 4: "draw", 5: "area" };
const TABS = {
  outline: ["Outline", "Outline one object at a time"],
  suggestions: ["Suggestions", "Find similar objects and review them one by one"],
  sweep: ["Sweep", "Search the whole image for one label"],
  review: ["Review", "Mark what is fully outlined, and review labeled areas"],
  table: ["Table", "Every outline on this image"],
};
const DEFAULT_CLEANUP = { fill_holes: false, smoothing: "off" };
// the tab each map tool belongs to
const MODE_TABS = { box: "outline", click: "outline", draw: "outline", area: "review" };
const DEFAULT_FOLDS = { images: true, layers: true, labels: true };
const DEFAULT_SEARCH = { useExample: true, useName: false, minScore: 0.3 };

function stored(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? { ...fallback, ...JSON.parse(value) } : fallback;
  } catch {
    return fallback;
  }
}
function store(key, value) {
  try {
    localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch {
    /* storage unavailable: settings just aren't remembered */
  }
}
function basename(path) {
  return path.split("/").pop();
}
function asMulti(geometry) {
  return geometry.type === "Polygon"
    ? { type: "MultiPolygon", coordinates: [geometry.coordinates] }
    : geometry;
}
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export default function App() {
  const [health, setHealth] = useState(null);
  const [images, setImages] = useState([]);
  const [imageId, setImageId] = useState(null);
  const [annotations, setAnnotations] = useState(null);
  const [areas, setAreas] = useState([]);
  const [waiting, setWaiting] = useState([]);
  const [labels, setLabels] = useState([]);
  const [imageLabels, setImageLabels] = useState([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [help, setHelp] = useState(false);

  const [mode, setMode] = useState("pan");
  const [label, setLabel] = useState("field");
  const [draft, setDraft] = useState(null);
  const [draftInfo, setDraftInfo] = useState(null);
  const [draftTool, setDraftTool] = useState("corners");

  const [tab, setTab] = useState("outline");
  const [visibility, setVisibility] = useState(() => stored("visibility", defaultVisibility));
  const [showLabels, setShowLabels] = useState(true);
  const [showCorners, setShowCorners] = useState(true);
  const [lookFor, setLookFor] = useState("");
  const [widths, setWidths] = useState(() => stored("widths", { left: 280, right: 340 }));
  const [filters, setFilters] = useState({ origin: "all", minScore: 0, jobId: null });
  const [suggestionId, setSuggestionId] = useState(null);
  const [search, setSearch] = useState(DEFAULT_SEARCH);
  const [folds, setFolds] = useState(() => stored("folds", DEFAULT_FOLDS));
  const [theme, setTheme] = useState(() => {
    try {
      const saved = localStorage.getItem("theme");
      if (saved === "light" || saved === "dark") return saved;
    } catch { /* no storage */ }
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  const [focus, setFocus] = useState(null);
  const [job, setJob] = useState(null);
  // sweep tile settings (null until the API's defaults arrive) and their tiles
  const [tiling, setTiling] = useState({ size: null, overlap: null });
  const [tileGrid, setTileGrid] = useState(null);

  useEffect(() => store("visibility", visibility), [visibility]);
  useEffect(() => store("widths", widths), [widths]);
  useEffect(() => store("folds", folds), [folds]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    store("theme", theme);
  }, [theme]);

  const image = images.find((i) => i.image_id === imageId) ?? null;
  const frame = useMemo(() => {
    if (!image) return null;
    try {
      return imageFrame(image);
    } catch (error) {
      setMessage(error.message);
      return null;
    }
  }, [image]);
  const modelReady = health?.status === "ready";

  const shownLayers = useMemo(
    () => visibleFeatures(
      annotations,
      { ...visibility, suggestions: visibility.suggestions || tab === "suggestions" },
      [draft?.annotationId, draft?.replaces].filter(Boolean)
    ),
    [annotations, visibility, tab, draft?.annotationId, draft?.replaces]
  );
  const queue = useMemo(
    () => reviewQueue(annotations, filters).filter((id) => id !== draft?.annotationId),
    [annotations, filters, draft?.annotationId]
  );
  const sweeps = useMemo(() => sweepsIn(annotations), [annotations]);

  // the tiles a sweep would use, refetched (after typing stops) when the
  // image or tile settings change. `tileGrid.key` says which image and
  // settings the grid is for; invalid settings give a grid with no tiles.
  const tilingKey = `${imageId}|${tiling.size}|${tiling.overlap}`;
  const loadedKey = useRef(null);
  useEffect(() => {
    if (!imageId || loadedKey.current === tilingKey) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      api.imageTiles(imageId, tiling.size, tiling.overlap)
        .then((grid) => {
          if (cancelled) return;
          // the first answer brings the API's defaults; keying the grid by
          // them means filling in the form does not fetch it again
          const key = `${imageId}|${grid.tile_size}|${grid.overlap}`;
          loadedKey.current = key;
          setTileGrid({ ...grid, key });
          setTiling((t) => (t.size == null ? { size: grid.tile_size, overlap: grid.overlap } : t));
        })
        .catch(() => {
          if (cancelled) return;
          loadedKey.current = tilingKey;
          setTileGrid({ key: tilingKey, tiles: [] });
        });
    }, 300);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [imageId, tilingKey, tiling.size, tiling.overlap]);
  // null while the grid for the current settings is loading
  const tileCount = tileGrid?.key === tilingKey ? tileGrid.tiles.length : null;
  // the one sweep tile on the map: dragged by the user, reset per image.
  // The tile grid covers the image, so its bounds are the image extent.
  const [tileCenter, setTileCenter] = useState(null);
  useEffect(() => setTileCenter(null), [imageId]);
  const imageExtent = useMemo(
    () => (frame && tileGrid?.tiles.length && tileGrid.key.startsWith(`${imageId}|`)
      ? extentOf({ type: "MultiPolygon",
                   coordinates: tileGrid.tiles.map((t) => frame.toMap(t.geometry).coordinates) })
      : null),
    [frame, tileGrid, imageId]
  );
  const shownTile = useMemo(() => {
    const pixel = frame?.georeferenced ? (image?.pixel_size ?? 1) : 1;
    const box = tileBox({ center: tileCenter, sizePx: tiling.size, overlapPx: tiling.overlap,
                          pixel, extent: imageExtent });
    if (!box) return null;
    const [x0, y0, x1, y1] = box.outer;
    const size = sizeText(Math.round((x1 - x0) / pixel), Math.round((y1 - y0) / pixel), image);
    return { ...box, label: `${size} · overlap ${lengthText(tiling.overlap || 0, image)}` };
  }, [frame, image, tileCenter, tiling.size, tiling.overlap, imageExtent]);
  const tileFeatures = useMemo(() => (tileGrid?.tiles ?? []).map((t) => {
    const [x0, y0, x1, y1] = t.pixel_box;
    return { geometry: t.geometry, size: sizeText(x1 - x0, y1 - y0, image) };
  }), [tileGrid, image]);
  const imageNames = useMemo(
    () => Object.fromEntries(images.map((i) => [i.image_id, basename(i.source_uri)])),
    [images]
  );

  // --- plumbing ------------------------------------------------------------

  const report = useCallback((error) => setMessage(error.message ?? String(error)), []);

  const run = useCallback(async (text, action) => {
    setBusy(true);
    setMessage(text);
    try {
      const result = await action();
      setMessage("");
      return result;
    } catch (error) {
      report(error);
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [report]);

  // changing tabs puts the map back to Pan / select, so a click on the map
  // never runs a tool from a tab that is no longer showing
  const switchTab = useCallback((value) => {
    setMode("pan");
    setTab(value);
  }, []);

  const refreshImages = useCallback(() => api.listImages().then(setImages).catch(report), [report]);

  // everything that depends on outlines reloads together
  const refresh = useCallback(() => {
    const shared = Promise.all([
      api.listAreas({ status: "submitted" }).then(setWaiting),
      api.listLabels().then(setLabels),
    ]);
    if (!imageId) return shared.catch(report);
    return Promise.all([
      shared,
      api.annotationsGeojson(imageId).then(setAnnotations),
      api.listAreas({ imageId }).then(setAreas),
      api.imageLabels(imageId).then(setImageLabels),
    ]).catch(report);
  }, [imageId, report]);

  useEffect(() => {
    const poll = () => api.health().then(setHealth).catch(() => setHealth({ status: "down" }));
    poll();
    const timer = setInterval(poll, 10000);
    return () => clearInterval(timer);
  }, []);

  // pick up anything dropped into data/images/ since the last visit
  useEffect(() => {
    api.scanImages().catch(() => {}).finally(refreshImages);
  }, [refreshImages]);

  useEffect(() => {
    setAnnotations(null);
    setAreas([]);
    setImageLabels([]);
    setDraft(null);
    setSuggestionId(null);
    refresh();
  }, [refresh]);

  // --- the draft (one object at a time) ------------------------------------

  const openFromRecord = useCallback((record, kind, extra = {}) => {
    const geometry = frame.toMap(asMulti(record.geometry));
    setDraft(newDraft({
      annotationId: record.annotation_id,
      geometry,
      kind,
      label: record.label,
      cleanup: record.prompt?.cleanup ?? DEFAULT_CLEANUP,
      ...extra,
    }));
    setDraftInfo({
      score: record.score,
      edit_iou: record.edit_iou,
      hasOriginal: Boolean(record.original_geometry),
    });
    // the editing tool stays as the user left it (e.g. Refine keeps refining)
    if (record.label && record.label !== "unlabeled") setLabel(record.label);
  }, [frame]);

  const openOutline = useCallback(async (id, kind) => {
    const record = await run("Opening…", () => api.getAnnotation(id));
    if (!record) return;
    openFromRecord(record, kind);
    switchTab("outline");
    setFocus({ extent: extentOf(frame.toMap(asMulti(record.geometry))), imageId: record.image_id,
               nonce: Date.now() });
  }, [run, openFromRecord, frame, switchTab]);

  // run SAM (box and/or clicks) and open the result as the draft
  const segment = useCallback(async ({ box, points, priorId, boxExtent, cleanup, replaces = null }) => {
    if (!modelReady) {
      setMessage("The model is not ready yet; see the status at the top right.");
      return;
    }
    const created = await run("SAM is outlining…", () => api.segment({
      imageId,
      box,
      points: points.map(({ x, y, label: l }) => ({ x, y, label: l })),
      label: label.trim() || "unlabeled",
      priorId,
      cleanup,
    }));
    if (!created) return;
    const record = await api.getAnnotation(created.annotation_id).catch(report);
    if (!record) return;
    openFromRecord(record, "model", { box, boxExtent, points, replaces });
    await refresh();
  }, [modelReady, run, imageId, label, report, openFromRecord, refresh]);

  const onBox = useCallback(async (extent) => {
    const box = boxToApi(extent, frame.pointToApi);
    const text = lookFor.trim();
    if (!text) {
      segment({ box, boxExtent: extent, points: [], cleanup: DEFAULT_CLEANUP });
      return;
    }
    // look for a named thing inside the box: best match to edit, rest to review
    if (!modelReady) {
      setMessage("The model is not ready yet; see the status at the top right.");
      return;
    }
    const result = await run(`Looking for “${text}” in the box…`, () =>
      api.findSimilar({ imageId, text, region: box, cleanup: DEFAULT_CLEANUP })
    );
    if (!result) return;
    if (!result.instances.length) {
      setMessage(`No “${text}” found inside the box. Try another name, or leave Look for empty.`);
      return;
    }
    const [best, ...rest] = [...result.instances].sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    const record = await api.getAnnotation(best.annotation_id).catch(report);
    if (!record) return;
    openFromRecord(record, "model", { box, boxExtent: extent });
    await refresh();
    setMessage(rest.length
      ? `Found ${result.instances.length}; editing the best. The other ${rest.length} are on the Suggestions tab.`
      : `Found 1 “${text}”.`);
  }, [frame, lookFor, segment, modelReady, run, imageId, report, openFromRecord, refresh]);

  const onPoint = useCallback((coordinate, { exclude }) => {
    const [x, y] = frame.pointToApi(coordinate);
    const point = { x, y, label: exclude ? 0 : 1, mapCoordinate: coordinate };
    if (draft) {
      if (draft.dirty && !window.confirm("Refining redraws the outline; your corner edits will be replaced. Continue?")) return;
      // an opened outline has no box yet: give SAM the area around it, so it
      // looks at that area at full detail
      let { box, boxExtent } = draft;
      if (!box) {
        const [x0, y0, x1, y1] = extentOf(draft.geometry);
        const pad = 0.25 * Math.max(x1 - x0, y1 - y0);
        boxExtent = [x0 - pad, y0 - pad, x1 + pad, y1 + pad];
        box = boxToApi(boxExtent, frame.pointToApi);
      }
      segment({
        box, boxExtent, points: [...draft.points, point], priorId: draft.annotationId,
        cleanup: draft.cleanup,
        // refining a saved or hand-drawn outline replaces it when saved
        replaces: draft.kind === "saved" ? draft.annotationId : draft.replaces,
      });
    } else {
      segment({ box: null, points: [point], cleanup: DEFAULT_CLEANUP });
    }
  }, [frame, draft, segment]);

  const onDraw = useCallback(async (geometry) => {
    const created = await run("Saving…", () =>
      api.createAnnotation({ image_id: imageId, geometry, label: label.trim() || "unlabeled" })
    );
    if (!created) return;
    openFromRecord(created, "saved");
    await refresh();
    setMessage("Drawn outline saved. Adjust it or change its label, then press Save.");
  }, [run, imageId, label, openFromRecord, refresh]);

  const onDraftChange = useCallback((geometry) => setDraft((d) => d && edit(d, geometry)), []);

  // click a corner to select it; Shift+click a second corner to select the
  // stretch between them (for Straighten)
  const onVertexSelect = useCallback((ref, { extend } = {}) => setDraft((d) => {
    if (!d) return d;
    if (extend && d.selectedVertex) return { ...d, rangeEnd: ref, flipSide: false, selectedHole: null };
    return { ...d, ...NO_SELECTION, selectedVertex: ref };
  }), []);
  const onHoleSelect = useCallback(
    (ref) => setDraft((d) => d && { ...d, ...NO_SELECTION, selectedHole: ref }), []
  );

  const removing = useMemo(() => {
    if (!draft?.selectedVertex || !draft.rangeEnd) return [];
    const ring = ringAt(draft.geometry, draft.selectedVertex.slice(0, 2));
    const indexes = cornersBetween(draft.geometry, draft.selectedVertex, draft.rangeEnd,
                                   draft.flipSide);
    return indexes ? indexes.map((k) => ring[k]) : [];
  }, [draft]);
  const highlight = useMemo(() => draft ? {
    selected: draft.selectedVertex && vertexAt(draft.geometry, draft.selectedVertex),
    rangeEnd: draft.rangeEnd && vertexAt(draft.geometry, draft.rangeEnd),
    removing,
    hole: draft.selectedHole && ringAt(draft.geometry, draft.selectedHole),
  } : {}, [draft, removing]);

  const straightenSelection = useCallback(() => {
    if (!draft?.rangeEnd) return;
    const geometry = straighten(draft.geometry, draft.selectedVertex, draft.rangeEnd,
                                draft.flipSide);
    if (!geometry) {
      setMessage("Select two corners on the same edge: click one, then Shift+click another.");
      return;
    }
    setDraft(edit(draft, geometry));
  }, [draft]);

  const simplifyDraft = useCallback((pixels) => {
    if (!draft || pixels === draft.simplifyLevel) return;
    const pixel = frame.georeferenced ? (image?.pixel_size ?? 1) : 1;
    const next = simplifyTo(draft, pixels, pixels * pixel);
    setDraft(next);
    const base = cornerCount(next.simplifyBase);
    setMessage(pixels
      ? `Simplified: ${base} → ${cornerCount(next.geometry)} corners.`
      : `Simplify off: ${base} corners.`);
  }, [draft, frame, image]);

  const removeSelectedHole = useCallback(() => {
    if (draft?.selectedHole) setDraft(edit(draft, removeHole(draft.geometry, draft.selectedHole)));
  }, [draft]);

  const onDraftShape = useCallback((shape, operation) => {
    setDraft((d) => {
      if (!d) return d;
      const geometry = combine(d.geometry, shape, operation);
      if (!geometry) {
        setMessage("That would erase the whole outline; use Cancel or Delete instead.");
        return d;
      }
      return edit(d, geometry);
    });
  }, []);

  // Delete key: the selected hole, the selected stretch, or the selected corner
  const deleteCorner = useCallback(() => {
    if (!draft) return;
    if (draft.selectedHole) return removeSelectedHole();
    if (draft.rangeEnd) return straightenSelection();
    if (!draft.selectedVertex) return;
    const geometry = deleteVertex(draft.geometry, draft.selectedVertex);
    if (!geometry) {
      setMessage("A shape needs at least three corners.");
      return;
    }
    setDraft(edit(draft, geometry));
  }, [draft, removeSelectedHole, straightenSelection]);

  const reclean = useCallback(async (cleanup) => {
    if (!draft) return;
    if (draft.dirty && !window.confirm("This redoes the outline from SAM's original; your corner edits will be replaced. Continue?")) return;
    const record = await run("Redoing outline…", () =>
      api.recleanAnnotation(draft.annotationId, cleanup)
    );
    if (!record) return;
    openFromRecord(record, draft.kind, {
      box: draft.box, boxExtent: draft.boxExtent, points: draft.points, replaces: draft.replaces,
    });
    await refresh();
  }, [draft, run, openFromRecord, refresh]);

  const closeDraft = useCallback(() => {
    setDraft(null);
    setDraftInfo(null);
  }, []);

  const save = useCallback(async () => {
    if (!draft || !label.trim()) return;
    const fields = { status: "accepted", label: label.trim() };
    if (draft.dirty) {
      fields.geometry = frame.toApi(draft.geometry);
      fields.edit_method = "vertex_edit";
    }
    const saved = await run("Saving…", async () => {
      const result = await api.updateAnnotation(draft.annotationId, fields);
      // a refined outline takes the place of the one it was refined from
      if (draft.replaces) await api.updateAnnotation(draft.replaces, { status: "rejected" });
      return result;
    });
    if (!saved) return;
    closeDraft();
    await refresh();
    setMessage(`Saved as “${saved.label}”. Mark the next object.`);
  }, [draft, label, frame, run, closeDraft, refresh]);

  const cancel = useCallback(async () => {
    if (!draft) return;
    if (draft.dirty && !window.confirm("Discard your changes to this outline?")) return;
    if (draft.kind === "model") {
      // an unsaved model outline: keep it as a rejected example, not a suggestion
      await run("Discarding…", () =>
        api.updateAnnotation(draft.annotationId, { status: "rejected" })
      );
    }
    closeDraft();
    await refresh();
  }, [draft, run, closeDraft, refresh]);

  const deleteOutline = useCallback(async (id) => {
    if (!window.confirm("Delete this outline for good? (Reject keeps it as a training example instead.)")) return false;
    const done = await run("Deleting…", async () => (await api.deleteAnnotation(id), true));
    await refresh();
    return Boolean(done);
  }, [run, refresh]);

  const deleteDraft = useCallback(async () => {
    if (draft && await deleteOutline(draft.annotationId)) closeDraft();
  }, [draft, deleteOutline, closeDraft]);

  const removeOutline = useCallback(async () => {
    if (!draft || !window.confirm("Reject this outline? It is kept as a rejected example for training.")) return;
    await run("Removing…", () => api.updateAnnotation(draft.annotationId, { status: "rejected" }));
    closeDraft();
    await refresh();
  }, [draft, run, closeDraft, refresh]);

  const selectImage = useCallback(async (id) => {
    if (id === imageId) return;
    if (draft?.kind === "model") {
      await api.updateAnnotation(draft.annotationId, { status: "rejected" }).catch(() => {});
    }
    setImageId(id);
  }, [imageId, draft]);

  // accept / reject / delete many outlines; true when done
  const bulkOutlines = useCallback(async (ids, action, acceptLabel = null) => {
    if (!ids.length) return false;
    const n = ids.length === 1 ? "1 outline" : `${ids.length} outlines`;
    const question = {
      accept: `Accept ${n}${acceptLabel ? ` as “${acceptLabel}”` : ""}?`,
      reject: `Reject ${n}? They are kept as training examples.`,
      delete: `Delete ${n} for good?`,
    }[action];
    if (!window.confirm(question)) return false;
    const result = await run("Saving…", () => api.bulkAnnotations(ids, action, acceptLabel));
    if (!result) return false;
    if (draft && ids.includes(draft.annotationId) && action === "delete") closeDraft();
    await refresh();
    setMessage(`${{ accept: "Accepted", reject: "Rejected", delete: "Deleted" }[action]} ${result.changed}.`);
    return true;
  }, [run, draft, closeDraft, refresh]);

  const resetImage = useCallback(async () => {
    const outlines = annotations?.features.length ?? 0;
    const complete = imageLabels.filter((l) => l.complete).length;
    if (!window.confirm(
      `Start over on ${imageNames[imageId]}? This deletes all ${outlines} outline(s), `
      + `${areas.length} labeled area(s) and ${complete} complete mark(s) on it, for good. `
      + "The image itself stays."
    )) return;
    const result = await run("Clearing…", () => api.resetImage(imageId));
    if (!result) return;
    try {
      localStorage.removeItem(`sweep:${imageId}`);
    } catch { /* no storage */ }
    setJob(null);
    closeDraft();
    await Promise.all([refresh(), refreshImages()]);
    setMessage(`Cleared ${result.annotations} outline(s), ${result.areas} area(s), `
               + `${result.labels} label mark(s).`);
  }, [annotations, imageLabels, areas, imageNames, imageId, run, closeDraft, refresh, refreshImages]);

  // --- suggestions -----------------------------------------------------------

  const showSuggestion = useCallback((id) => {
    setSuggestionId(id);
    if (id) setFocus({ id, layer: "suggestions", imageId, nonce: Date.now() });
  }, [imageId]);

  const step = useCallback(
    (direction) => showSuggestion(nextId(queue, suggestionId, direction)),
    [queue, suggestionId, showSuggestion]
  );

  // fields: what to save on the current suggestion, or null to delete it
  const reviewSuggestion = useCallback(async (fields) => {
    const index = queue.indexOf(suggestionId);
    if (index < 0) return;
    const rest = queue.filter((id) => id !== suggestionId);
    const following = rest[index] ?? rest[0] ?? null;
    await run("Saving…", () => (fields
      ? api.updateAnnotation(suggestionId, fields)
      : api.deleteAnnotation(suggestionId)));
    await refresh();
    showSuggestion(following);
  }, [queue, suggestionId, run, refresh, showSuggestion]);

  const exampleId = draft?.kind === "saved" ? draft.annotationId : null;
  const findSimilar = useCallback(async ({ withExample, byName }) => {
    const result = await run("Looking for similar objects…", () => api.findSimilar({
      imageId,
      text: byName ? label.trim() : null,
      exampleIds: withExample ? [exampleId] : [],
      cleanup: DEFAULT_CLEANUP,
      minScore: search.minScore,
      skipExisting: true,
    }));
    if (!result) return;
    await refresh();
    setFilters({ origin: "similar", minScore: 0, jobId: null });
    const repeats = result.skipped_existing
      ? ` ${result.skipped_existing} more matched outlines already on the image.` : "";
    setMessage(`Found ${result.instances.length} new suggestion(s).${repeats}`);
    const best = [...result.instances].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];
    showSuggestion(best?.annotation_id ?? null);
  }, [run, imageId, label, exampleId, search.minScore, refresh, showSuggestion]);

  // --- sweeps ---------------------------------------------------------------

  useEffect(() => {
    setJob(null);
    let jobId = null;
    try {
      jobId = imageId && localStorage.getItem(`sweep:${imageId}`);
    } catch { /* no storage */ }
    if (jobId) api.getJob(jobId).then(setJob).catch(() => setJob(null));
  }, [imageId]);

  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const timer = setTimeout(async () => {
      try {
        const latest = await api.getJob(job.job_id);
        setJob(latest);
        if (latest.status === "succeeded") {
          await refresh();
          setMessage(`Sweep finished: ${latest.result?.annotation_count ?? 0} suggestions to review.`);
        }
      } catch (error) {
        report(error);
      }
    }, 3000);
    return () => clearTimeout(timer);
  }, [job, refresh, report]);

  const startSweep = useCallback(async ({ tileSize, overlap, exampleId }) => {
    const created = await run("Starting sweep…", () =>
      api.startSweep({ imageId, text: label.trim(), tileSize, overlap, exampleId })
    );
    if (!created) return;
    store(`sweep:${imageId}`, created.job_id);
    setJob({ job_id: created.job_id, status: created.status });
  }, [run, imageId, label]);

  const reviewSweep = useCallback((jobId) => {
    const next = { origin: "sweep", minScore: 0, jobId };
    setFilters(next);
    switchTab("suggestions");
    showSuggestion(reviewQueue(annotations, next)[0] ?? null);
  }, [annotations, showSuggestion, switchTab]);

  // --- areas, labels, images -------------------------------------------------

  const drawArea = useCallback(async (geometry) => {
    const created = await run("Saving area…", () =>
      api.createArea({ imageId, label: label.trim(), geometry })
    );
    setMode("pan");
    await refresh();
    if (created) setMessage(`Area saved. Outline every “${label}” inside it, then submit it.`);
  }, [run, imageId, label, refresh]);

  const zoomArea = useCallback((area) => {
    selectImage(area.image_id);
    setFocus({ id: area.area_id, layer: "areas", imageId: area.image_id, nonce: Date.now() });
  }, [selectImage]);

  const actOnArea = useCallback(async (area, action) => {
    if (action === "delete") {
      if (!window.confirm(`Delete this “${area.label}” area? Its outlines are kept.`)) return;
      await run("Deleting area…", () => api.deleteArea(area.area_id));
    } else {
      let note;
      if (action === "return") {
        note = window.prompt("What needs fixing?");
        if (note === null) return;
      }
      await run("Saving…", () => api.areaAction(area.area_id, action, note));
    }
    await refresh();
  }, [run, refresh]);

  const toggleComplete = useCallback(async (name, complete) => {
    await run("Saving…", () => api.setImageLabel(imageId, name, complete));
    await Promise.all([refresh(), refreshImages()]);
  }, [run, imageId, refresh, refreshImages]);

  const renameLabel = useCallback(async (old) => {
    const next = window.prompt(`Rename “${old}” on every image to:`, old);
    if (next === null || !next.trim() || next.trim() === old) return;
    const result = await run("Renaming…", () => api.renameLabel({ old, new: next.trim() }));
    if (!result) return;
    if (label === old) setLabel(next.trim());
    await Promise.all([refresh(), refreshImages()]);
    setMessage(`Renamed ${result.annotations} outline(s) and ${result.areas} area(s).`);
  }, [run, label, refresh, refreshImages]);

  const scan = useCallback(async () => {
    const result = await run("Scanning data/images/…", () => api.scanImages());
    if (!result) return;
    const notes = [`${result.registered.length} new image(s)`];
    if (result.skipped.length) notes.push(`skipped (not GeoTIFF/PNG): ${result.skipped.join(", ")}`);
    if (result.failed.length) notes.push(`failed: ${result.failed.join("; ")}`);
    setMessage(notes.join(" · "));
    await refreshImages();
  }, [run, refreshImages]);

  const upload = useCallback(async (file) => {
    const created = await run(`Uploading ${file.name}…`, () => api.uploadImage(file));
    await refreshImages();
    if (created) selectImage(created.image_id);
  }, [run, refreshImages, selectImage]);

  const saveProvenance = useCallback(async (id, fields) => {
    const updated = await run("Saving…", () => api.updateImage(id, fields));
    await refreshImages();
    return Boolean(updated);
  }, [run, refreshImages]);

  const updateOutline = useCallback(async (id, fields) => {
    await run("Saving…", () => api.updateAnnotation(id, fields));
    await refresh();
  }, [run, refresh]);

  // a tool opens its own tab (Box, Click, Draw: Outline; Area: Review)
  const chooseMode = useCallback((value) => {
    setMode(value);
    if (MODE_TABS[value]) setTab(MODE_TABS[value]);
  }, []);

  // --- keyboard ----------------------------------------------------------------

  useEffect(() => {
    const onKey = (event) => {
      const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName);
      if (help) {
        if (event.key === "Escape") setHelp(false);
        return;
      }
      if (draft && event.key === "Enter") { event.preventDefault(); save(); return; }
      if (draft && event.key === "Escape") { cancel(); return; }
      if (typing) return;
      const key = event.key.toLowerCase();
      if (event.key === "?") setHelp(true);
      else if (draft) {
        if ((event.ctrlKey || event.metaKey) && key === "z") { event.preventDefault(); setDraft(undo(draft)); }
        else if (event.key === "Delete" || event.key === "Backspace") deleteCorner();
        else if (key === "v") setShowCorners((v) => !v);
        else if (key === "e") setDraftTool("corners");
        else if (key === "x") setDraftTool("erase");
        else if (key === "a") setDraftTool("add");
        else if (key === "s") straightenSelection();
        else if (key === "f" && modelReady) setDraftTool("refine");
        else if (key === "h" && draftInfo?.hasOriginal) reclean({ ...draft.cleanup, fill_holes: !draft.cleanup.fill_holes });
      } else if (MODE_KEYS[event.key]) chooseMode(MODE_KEYS[event.key]);
      else if (tab === "suggestions") {
        if (key === "n") step(1);
        else if (key === "p") step(-1);
        else if (key === "a") reviewSuggestion({ status: "accepted", label: label.trim() });
        else if (key === "x") reviewSuggestion({ status: "rejected" });
        else if (key === "d") reviewSuggestion(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [help, draft, draftInfo, modelReady, tab, label, save, cancel, deleteCorner, reclean, chooseMode,
      step, reviewSuggestion, straightenSelection]);

  // --- layout ----------------------------------------------------------------

  const gridStyle = { gridTemplateColumns: `${widths.left}px 6px 1fr 6px ${widths.right}px` };
  const dragLeft = useCallback((x) => setWidths((w) => ({ ...w, left: clamp(x, 180, 560) })), []);
  const dragRight = useCallback(
    (x) => setWidths((w) => ({ ...w, right: clamp(window.innerWidth - x, 260, 680) })), []
  );

  return (
    <div className="app" style={gridStyle}>
      <header>
        <h1>Segmentation Assistant</h1>
        <div className="row tight">
          <span className={`health ${health?.status ?? "unknown"}`}
                title="Whether SAM-3 is loaded and ready on the server">
            model: {health?.status ?? "…"}{health?.error ? ` (${health.error})` : ""}
          </span>
          <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                  title={`Switch to the ${theme === "dark" ? "light" : "dark"} theme`}>
            {theme === "dark" ? "☀ Light" : "☾ Dark"}
          </button>
          <button onClick={() => setHelp(true)} title="How to use this tool (shortcut: ?)">? Help</button>
        </div>
      </header>

      <aside className="left">
        <Fold title="Images" open={folds.images} onToggle={(open) => setFolds((f) => ({ ...f, images: open }))}>
          <ImageList images={images} selectedId={imageId} onSelect={selectImage}
                     onUpload={upload} onScan={scan} onSaveProvenance={saveProvenance} />
          {image && (
            <button className="danger start-over" onClick={resetImage}
                    title="Delete every outline, labeled area and complete mark on this image">
              Start over on this image…
            </button>
          )}
        </Fold>
        {image && (
          <Fold title="Show on map" open={folds.layers}
                onToggle={(open) => setFolds((f) => ({ ...f, layers: open }))}>
            <LayersPanel visibility={visibility} onChange={setVisibility}
                         showLabels={showLabels} onShowLabels={setShowLabels}
                         showCorners={showCorners} onShowCorners={setShowCorners} />
          </Fold>
        )}
        <Fold title="Labels" open={folds.labels} onToggle={(open) => setFolds((f) => ({ ...f, labels: open }))}>
          <LabelsPanel labels={labels} hiddenLabels={visibility.hiddenLabels}
                       onToggleLabel={(l) => setVisibility((v) => toggleLabel(v, l))}
                       onRename={renameLabel} />
        </Fold>
      </aside>
      <Splitter side="left" onDrag={dragLeft} />

      <main>
        {image ? (
          <MapView
            image={image}
            frame={frame}
            saved={shownLayers.saved}
            suggestions={shownLayers.suggestions}
            showLabels={showLabels}
            areas={visibility.areas ? areas : []}
            tiles={visibility.tiles && visibility.allTiles ? tileFeatures : []}
            tileBox={visibility.tiles ? shownTile : null}
            onTileMove={setTileCenter}
            draft={draft}
            draftTool={draftTool}
            mode={mode}
            selectedSuggestionId={suggestionId}
            focus={focus}
            onPick={openOutline}
            onBox={onBox}
            onPoint={onPoint}
            onDraw={onDraw}
            onDrawArea={drawArea}
            onDraftChange={onDraftChange}
            onVertexSelect={onVertexSelect}
            onHoleSelect={onHoleSelect}
            onDraftShape={onDraftShape}
            highlight={highlight}
            showCorners={showCorners}
          />
        ) : (
          <p className="muted center">Pick an image on the left, or press ? for help.</p>
        )}
      </main>
      <Splitter side="right" onDrag={dragRight} />

      <aside className="right">
        {image && (
          <>
            <nav className="tabs">
              {Object.entries(TABS).map(([value, [text, hint]]) => (
                <button key={value} className={tab === value ? "active" : ""} title={hint}
                        onClick={() => switchTab(value)}>
                  {text}{value === "suggestions" && queue.length ? ` (${queue.length})` : ""}
                </button>
              ))}
            </nav>
            {tab !== "outline" && (
              <label className="field">
                Label
                <input list="label-options" value={label} onChange={(e) => setLabel(e.target.value)} />
                <datalist id="label-options">
                  {labels.map((l) => <option key={l.label} value={l.label} />)}
                </datalist>
              </label>
            )}
            {tab === "outline" && (
              <OutlinePanel
                mode={mode} onMode={chooseMode} modelReady={modelReady}
                draft={draft} draftInfo={draftInfo} draftTool={draftTool} onDraftTool={setDraftTool}
                label={label} onLabel={setLabel} labels={labels}
                onFillHoles={(fill) => reclean({ ...draft.cleanup, fill_holes: fill })}
                onSmoothing={(smoothing) => reclean({ ...draft.cleanup, smoothing })}
                onUndo={() => setDraft(undo(draft))} onDeleteCorner={deleteCorner}
                onSave={save} onCancel={cancel} onRemove={removeOutline} onDelete={deleteDraft}
                removingCount={removing.length} onStraighten={straightenSelection}
                onFlip={() => setDraft((d) => d && { ...d, flipSide: !d.flipSide })}
                onSimplify={simplifyDraft} onRemoveHole={removeSelectedHole}
                lookFor={lookFor} onLookFor={setLookFor}
                showCorners={showCorners} onShowCorners={setShowCorners}
              />
            )}
            {tab === "suggestions" && (
              <SuggestionsPanel
                label={label} exampleId={exampleId} search={search}
                onSearch={setSearch} onFindSimilar={findSimilar} modelReady={modelReady}
                queue={queue} currentIndex={queue.indexOf(suggestionId)}
                filters={filters} onFilters={setFilters} onStep={step}
                onAccept={() => reviewSuggestion({ status: "accepted", label: label.trim() })}
                onReject={() => reviewSuggestion({ status: "rejected" })}
                onDelete={() => reviewSuggestion(null)}
                onEdit={() => suggestionId && openOutline(suggestionId, "suggestion")}
                onBulk={(action) => bulkOutlines(queue, action, action === "accept" ? label.trim() : null)}
              />
            )}
            {tab === "sweep" && (
              <SweepPanel image={image} label={label} modelReady={modelReady} job={job}
                          tiling={tiling} onTiling={setTiling}
                          tileCount={tileCount}
                          exampleId={exampleId}
                          onStart={startSweep} sweeps={sweeps} onReview={reviewSweep}
                          onBulk={bulkOutlines} />
            )}
            {tab === "review" && (
              <CompletePanel imageLabels={imageLabels} allLabels={labels} onToggle={toggleComplete} />
            )}
            {tab === "review" && (
              <AreasPanel label={label} mode={mode}
                          onDrawMode={() => chooseMode(mode === "area" ? "pan" : "area")}
                          areas={areas} waiting={waiting} imageNames={imageNames}
                          onZoom={zoomArea} onAction={actOnArea} />
            )}
            {tab === "table" && (
              <TablePanel annotations={annotations} selectedId={draft?.annotationId}
                          onShow={(id) => {
                            const status = annotations?.features.find((f) => f.id === id)?.properties.status;
                            openOutline(id, status === "proposed" ? "suggestion" : "saved");
                          }}
                          onUpdate={updateOutline}
                          onDelete={(id) => deleteOutline(id).then((done) => {
                            if (done && draft?.annotationId === id) closeDraft();
                          })}
                          onBulk={bulkOutlines} />
            )}
          </>
        )}
      </aside>

      <footer>{busy ? "Working… " : ""}{message}</footer>
      {help && <HelpDialog onClose={() => setHelp(false)} />}
    </div>
  );
}

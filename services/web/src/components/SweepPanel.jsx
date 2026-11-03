// Full-image sweeps: SAM-3 finds every object of a label across the whole
// image, tile by tile, without shrinking it. Results join the review queue.
import { useState } from "react";

import { areaText, lengthText, metresPerPixel, pixelSizeText } from "../imageInfo.js";

// an emptied number box stays empty instead of turning into 0
const numberOrEmpty = (value) => (value === "" ? "" : Number(value));

// which of a sweep's outlines "Delete" removes
const DELETE_WHICH = {
  proposed: "unreviewed",
  rejected: "rejected",
  accepted: "accepted",
  all: "all",
};

// "1008 px (10.08 km)", or "1008 px" when the image has no size in metres
function pixelsAndMetres(px, image) {
  return metresPerPixel(image) ? `${px} px (${lengthText(px, image)})` : `${px} px`;
}

// "No size limits", "At least 5000 m²", "500 m² to 2 km²"; null for sweeps
// made before limits were recorded
function sizeLimits(params, image) {
  if (!("min_size" in params)) return null;
  const { min_size: min, max_size: max } = params;
  if (!min && max == null) return "No size limits";
  if (!min) return `At most ${areaText(max, image)}`;
  if (max == null) return `At least ${areaText(min, image)}`;
  return `${areaText(min, image)} to ${areaText(max, image)}`;
}

// One sweep: its settings and outlines. Clicking the card selects its
// outlines on the map; the color swatch recolors them (this browser only).
function SweepItem({ sweep, image, color, onColor, selected, onSelect, onReview, onBulk }) {
  const [which, setWhich] = useState("proposed");
  const { job } = sweep;
  const params = job.params ?? {};
  const limits = sizeLimits(params, image);
  const when = job.created_at
    ? new Date(job.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
    : null;
  const doomed = which === "all"
    ? [...sweep.ids.proposed, ...sweep.ids.accepted, ...sweep.ids.rejected]
    : sweep.ids[which];
  return (
    <li className={`box sweep-card${selected ? " selected" : ""}`} style={{ borderLeftColor: color }}
        onClick={onSelect} title="Select this sweep's outlines on the map (click again to clear)">
      <div className="row tight">
        <input type="color" value={color} title="Color of this sweep's outlines"
               onClick={(e) => e.stopPropagation()} onChange={(e) => onColor(e.target.value)} />
        <b>{sweep.label}</b>
        <span className="muted small">{when ?? sweep.jobId.slice(0, 8)}</span>
      </div>
      {params.tile_size && (
        <div className="small muted">
          Tiles {pixelsAndMetres(params.tile_size, image)} · overlap {pixelsAndMetres(params.overlap, image)}
        </div>
      )}
      {limits && <div className="small muted">{limits}</div>}
      {params.exemplars?.length > 0 && <div className="small muted">Guided by an example outline</div>}
      {job.status !== "succeeded" && (
        <div className="small"><b>{job.status}</b>{job.error && ` · ${job.error}`}</div>
      )}
      <div className="small muted">
        {sweep.proposed} to review · {sweep.accepted} accepted · {sweep.rejected} rejected
      </div>
      <div className="row" onClick={(e) => e.stopPropagation()}>
        <button onClick={() => onReview(sweep.jobId)} disabled={!sweep.proposed}>Review</button>
        <button onClick={() => onBulk(sweep.ids.proposed, "accept")} disabled={!sweep.proposed}
                title={`Accept every unreviewed outline as “${sweep.label}”`}>Accept all</button>
        <button onClick={() => onBulk(sweep.ids.proposed, "reject")} disabled={!sweep.proposed}
                title="Reject every unreviewed outline (kept as training examples)">Reject all</button>
      </div>
      <div className="row tight" onClick={(e) => e.stopPropagation()}>
        <button className="danger" onClick={() => onBulk(doomed, "delete")} disabled={!doomed.length}
                title="Delete these outlines for good">
          Delete {doomed.length}
        </button>
        <select value={which} onChange={(e) => setWhich(e.target.value)} title="Which outlines to delete">
          {Object.entries(DELETE_WHICH).map(([value, text]) => (
            <option key={value} value={value}>{text}</option>
          ))}
        </select>
      </div>
    </li>
  );
}

export default function SweepPanel({
  image,
  label,
  modelReady,
  job,
  tiling,
  onTiling,
  tileCount,
  exampleId,
  onStart,
  sweeps,
  colorOf,
  onColor,
  selectedSweep,
  onSelectSweep,
  onReview,
  onBulk,
}) {
  const running = job && (job.status === "queued" || job.status === "running");
  const [useExample, setUseExample] = useState(true);
  const withExample = useExample && Boolean(exampleId);
  const pixel = pixelSizeText(image);

  return (
    <section>
      <p className="explain">
        A sweep searches the entire image for one label, piece by piece, without shrinking
        it. Results become suggestions (Suggestions tab). Best for objects SAM recognizes by
        name, like buildings or water.
      </p>
      <h2>Sweep the whole image</h2>
      <div className="box">
        <p className="small">
          {image.width} × {image.height} px{pixel ? ` · ${pixel} per pixel` : ""}
        </p>
        <p className="muted small">
          Looks for every “{label || "…"}” across the image in overlapping tiles. Can take
          minutes. Objects cut by a tile edge are joined back into one outline.
        </p>
        <label className="check"
               title="Open a saved outline first (Pan / select, click it) to use it as the example">
          <input type="checkbox" checked={withExample} disabled={!exampleId}
                 onChange={(e) => setUseExample(e.target.checked)} />
          Sweep for things that look like the open saved outline
        </label>
        {withExample && (
          <p className="muted small">
            The example guides the tile it is in; other tiles search by the name “{label}”.
          </p>
        )}
        <div className="row">
          <label className="field inline">
            Tile size
            <input type="number" min="64" value={tiling.size ?? ""}
                   onChange={(e) => onTiling({ ...tiling, size: numberOrEmpty(e.target.value) })} />
          </label>
          <label className="field inline">
            Overlap
            <input type="number" min="0" value={tiling.overlap ?? ""}
                   onChange={(e) => onTiling({ ...tiling, overlap: numberOrEmpty(e.target.value) })} />
          </label>
        </div>
        {tileCount === null ? (
          <p className="muted small">…</p>
        ) : tileCount ? (
          <p className="muted small">
            {tileCount} tile{tileCount === 1 ? "" : "s"} (Show on map → Sweep tile)
          </p>
        ) : (
          <p className="muted small">Overlap must be smaller than the tile size (at least 64).</p>
        )}
        <button className="primary"
                disabled={!modelReady || !label || running || !tileCount || tiling.size === "" || tiling.overlap === ""}
                onClick={() => onStart({ tileSize: tiling.size, overlap: tiling.overlap,
                                         exampleId: withExample ? exampleId : null })}>
          {running ? "Sweep running…" : withExample ? `Sweep similar “${label}”` : `Sweep for “${label}”`}
        </button>
        {job && (
          <p className="small">
            Last sweep: <b>{job.status}</b>
            {job.result?.annotation_count != null && ` · ${job.result.annotation_count} outlines`}
            {job.error && ` · ${job.error}`}
            {job.result?.ingest_error && ` · ${job.result.ingest_error}`}
          </p>
        )}
      </div>

      <h2>Sweeps to review</h2>
      {sweeps.length === 0 ? (
        <p className="muted small">Nothing left to review from sweeps on this image.</p>
      ) : (
        <ul className="plain">
          {sweeps.map((s) => (
            <SweepItem key={s.jobId} sweep={s} image={image} color={colorOf(s.jobId)}
                       onColor={(color) => onColor(s.jobId, color)}
                       selected={s.jobId === selectedSweep} onSelect={() => onSelectSweep(s.jobId)}
                       onReview={onReview} onBulk={onBulk} />
          ))}
        </ul>
      )}
    </section>
  );
}

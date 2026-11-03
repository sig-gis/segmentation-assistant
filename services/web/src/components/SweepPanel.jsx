// Full-image sweeps: SAM-3 finds every object of a label across the whole
// image, tile by tile, without shrinking it. Results join the review queue.
import { useState } from "react";

import { pixelSizeText } from "../imageInfo.js";

// an emptied number box stays empty instead of turning into 0
const numberOrEmpty = (value) => (value === "" ? "" : Number(value));

// which of a sweep's outlines "Delete" removes
const DELETE_WHICH = {
  proposed: "unreviewed",
  rejected: "rejected",
  accepted: "accepted",
  all: "all",
};

function SweepItem({ sweep, onReview, onBulk }) {
  const [which, setWhich] = useState("proposed");
  const doomed = which === "all"
    ? [...sweep.ids.proposed, ...sweep.ids.accepted, ...sweep.ids.rejected]
    : sweep.ids[which];
  return (
    <li className="box">
      <div><b>{sweep.label}</b> <span className="muted small">{sweep.jobId.slice(0, 8)}</span></div>
      <div className="small muted">
        {sweep.proposed} to review · {sweep.accepted} accepted · {sweep.rejected} rejected
      </div>
      <div className="row">
        <button onClick={() => onReview(sweep.jobId)} disabled={!sweep.proposed}>Review</button>
        <button onClick={() => onBulk(sweep.ids.proposed, "accept")} disabled={!sweep.proposed}
                title={`Accept every unreviewed outline as “${sweep.label}”`}>Accept all</button>
        <button onClick={() => onBulk(sweep.ids.proposed, "reject")} disabled={!sweep.proposed}
                title="Reject every unreviewed outline (kept as training examples)">Reject all</button>
      </div>
      <div className="row tight">
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
        {tileCount ? (
          <p className="muted small">
            {tileCount} tile{tileCount === 1 ? "" : "s"} (Show on map → Sweep tiles)
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

      <h2>Sweeps on this image</h2>
      {sweeps.length === 0 ? (
        <p className="muted small">None yet.</p>
      ) : (
        <ul className="plain">
          {sweeps.map((s) => (
            <SweepItem key={s.jobId} sweep={s} onReview={onReview} onBulk={onBulk} />
          ))}
        </ul>
      )}
    </section>
  );
}

// Labeled areas: mark a region where every object of a label is outlined,
// submit it, and review submitted areas (approve, or return with a note).
// Only approved areas count as complete training data.
import { AREA_COLORS } from "../styles.js";

const ACTIONS = {
  draft: ["submit", "delete"],
  returned: ["submit", "delete"],
  submitted: ["approve", "return", "reopen"],
  approved: ["reopen"],
};

const ACTION_TEXT = {
  submit: "Submit for review",
  approve: "Approve",
  return: "Return…",
  reopen: "Reopen",
  delete: "Delete",
};

function Status({ status }) {
  return (
    <span className="badge" style={{ borderColor: AREA_COLORS[status], color: AREA_COLORS[status] }}>
      {status}
    </span>
  );
}

function AreaItem({ area, onZoom, onAction, imageName }) {
  const { proposed, accepted } = area.counts;
  return (
    <li className="box">
      <div className="row between">
        <b>{area.label}</b>
        <Status status={area.status} />
      </div>
      {imageName && <div className="small muted">{imageName}</div>}
      <div className="small muted">
        {accepted} accepted · {proposed} still to review
      </div>
      {area.note && <div className="small note">Reviewer: {area.note}</div>}
      <div className="row">
        <button onClick={() => onZoom(area)}>Zoom</button>
        {ACTIONS[area.status].map((action) => (
          <button key={action} className={action === "delete" ? "danger" : ""}
                  onClick={() => onAction(area, action)}>
            {ACTION_TEXT[action]}
          </button>
        ))}
      </div>
    </li>
  );
}

export default function AreasPanel({
  label,
  mode,
  onDrawMode,
  areas,
  waiting,
  imageNames,
  onZoom,
  onAction,
}) {
  return (
    <section>
      <p className="explain">
        For large images: mark a rectangle, outline every “{label || "…"}” inside it, and
        submit it for review. A reviewer approves it or returns it with a note. This does
        not search for anything; it records that a region is finished. Use “Complete for
        this image” (above) instead when the whole image is done.
      </p>
      <h2>Labeled areas</h2>
      <div className="box">
        <p className="muted small">
          Drag a rectangle around a region, then outline every “{label || "…"}” inside it and
          submit. Only approved areas are used for training.
        </p>
        <button className={mode === "area" ? "active" : ""} onClick={onDrawMode}
                disabled={!label} title="Shortcut: 5">
          {mode === "area" ? "Drag on the map…" : `Draw a “${label}” area`}
        </button>
      </div>
      {areas.length === 0 ? (
        <p className="muted small">No areas on this image.</p>
      ) : (
        <ul className="plain">
          {areas.map((area) => (
            <AreaItem key={area.area_id} area={area} onZoom={onZoom} onAction={onAction} />
          ))}
        </ul>
      )}

      <h2>Waiting for review (all images)</h2>
      {waiting.length === 0 ? (
        <p className="muted small">Nothing submitted.</p>
      ) : (
        <ul className="plain">
          {waiting.map((area) => (
            <AreaItem key={area.area_id} area={area} onZoom={onZoom} onAction={onAction}
                      imageName={imageNames[area.image_id]} />
          ))}
        </ul>
      )}
    </section>
  );
}

// Every outline on this image as a table: reviewed outlines, then proposed
// ones grouped by the sweep that made them. Jump to one, assign its training
// split, or tick several and accept, reject or delete them.
import { useEffect, useState } from "react";

import { tableSections } from "../review.js";

const SPLITS = ["unassigned", "train", "val", "test"];

function percent(value) {
  return value == null ? "–" : `${Math.round(value * 100)}%`;
}

// One table of outlines; its header box ticks or unticks all of its rows.
function OutlineTable({ rows, ticked, onTicked, toggle, selectedId, onShow, onUpdate, onDelete }) {
  const allTicked = rows.length > 0 && rows.every((f) => ticked.has(f.id));
  const tickAll = () => onTicked((t) => {
    const next = new Set(t);
    rows.forEach((f) => (allTicked ? next.delete(f.id) : next.add(f.id)));
    return next;
  });
  return (
    <table className="table">
      <thead>
        <tr>
          <th>
            <input type="checkbox" checked={allTicked} disabled={!rows.length}
                   title="Tick every row in this table" onChange={tickAll} />
          </th>
          <th>Label</th><th>Status</th><th>Conf.</th><th>Split</th><th />
        </tr>
      </thead>
      <tbody>
        {rows.map((f) => (
          <tr key={f.id} className={f.id === selectedId ? "selected" : ""}
              onClick={() => onShow(f.id)}
              title={`${f.properties.source} · ${f.properties.edit_method ?? ""}`}>
            <td onClick={(e) => e.stopPropagation()}>
              <input type="checkbox" checked={ticked.has(f.id)} onChange={() => toggle(f.id)} />
            </td>
            <td>{f.properties.label}</td>
            <td className={`status ${f.properties.status}`}>{f.properties.status}</td>
            <td>{percent(f.properties.score)}</td>
            <td onClick={(e) => e.stopPropagation()}>
              <select value={f.properties.split}
                      onChange={(e) => onUpdate(f.id, { split: e.target.value })}>
                {SPLITS.map((s) => <option key={s}>{s}</option>)}
              </select>
            </td>
            <td onClick={(e) => e.stopPropagation()}>
              <div className="row tight nowrap">
                {f.properties.status !== "rejected" && (
                  <button className="small-button" title="Reject (keep as a training example)"
                          onClick={() => onUpdate(f.id, { status: "rejected" })}>✕</button>
                )}
                <button className="danger small-button" title="Delete for good"
                        onClick={() => onDelete(f.id)}>🗑</button>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// "Sweep “field” · Nov 2, 5:11 PM", or "Not from a sweep"
function groupTitle(group, sweeps) {
  if (!group.jobId) return "Not from a sweep";
  const created = sweeps.find((s) => s.jobId === group.jobId)?.job?.created_at;
  const when = created
    ? ` · ${new Date(created).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`
    : "";
  return `Sweep “${group.label}”${when}`;
}

export default function TablePanel({
  annotations, sweeps, colorOf, selectedId, onShow, onUpdate, onDelete, onBulk,
}) {
  const [ticked, setTicked] = useState(new Set());
  const { reviewed, proposed } = tableSections(annotations, sweeps);
  // only outlines still on the image can stay ticked
  useEffect(() => {
    const shown = new Set((annotations?.features ?? []).map((f) => f.id));
    setTicked((t) => {
      const kept = new Set([...t].filter((id) => shown.has(id)));
      return kept.size === t.size ? t : kept;
    });
  }, [annotations]);

  const toggle = (id) => setTicked((t) => {
    const next = new Set(t);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const bulk = async (action) => {
    if (await onBulk([...ticked], action)) setTicked(new Set());
  };
  const tableProps = { ticked, onTicked: setTicked, toggle, selectedId, onShow, onUpdate, onDelete };
  const proposedCount = proposed.reduce((n, g) => n + g.features.length, 0);

  return (
    <section>
      <p className="explain">Every outline on this image. Click a row to open it on the
        map. ✕ rejects it (kept as a training example), 🗑 deletes it for good. Tick rows
        to accept, reject or delete several at once.</p>
      {ticked.size > 0 && (
        <div className="row bulk-bar">
          <span className="grow-left small">{ticked.size} ticked</span>
          <button onClick={() => bulk("accept")} title="Accept, keeping each outline's label">
            Accept
          </button>
          <button onClick={() => bulk("reject")} title="Reject (kept as training examples)">
            Reject
          </button>
          <button className="danger" onClick={() => bulk("delete")} title="Delete for good">
            Delete
          </button>
        </div>
      )}

      <h2>Reviewed ({reviewed.length})</h2>
      {reviewed.length
        ? <OutlineTable rows={reviewed} {...tableProps} />
        : <p className="muted small">No accepted or rejected outlines yet.</p>}

      <h2>Proposed ({proposedCount})</h2>
      {proposed.length === 0 && <p className="muted small">Nothing waiting for review.</p>}
      {proposed.map((group) => (
        <details key={group.jobId ?? "other"} className="table-group">
          <summary style={{ borderLeftColor: group.jobId ? colorOf(group.jobId) : undefined }}>
            {groupTitle(group, sweeps)} ({group.features.length})
          </summary>
          <OutlineTable rows={group.features} {...tableProps} />
        </details>
      ))}
    </section>
  );
}

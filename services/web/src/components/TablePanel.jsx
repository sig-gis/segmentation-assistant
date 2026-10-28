// Every outline on this image as a table: filter by status, jump to one,
// assign its training split, or tick several and accept, reject or delete them.
import { useEffect, useState } from "react";

const SPLITS = ["unassigned", "train", "val", "test"];

function percent(value) {
  return value == null ? "–" : `${Math.round(value * 100)}%`;
}

export default function TablePanel({ annotations, selectedId, onShow, onUpdate, onDelete, onBulk }) {
  const [status, setStatus] = useState("all");
  const [ticked, setTicked] = useState(new Set());
  const rows = (annotations?.features ?? []).filter(
    (f) => status === "all" || f.properties.status === status
  );
  // only rows still shown can stay ticked
  useEffect(() => {
    const shown = new Set(rows.map((f) => f.id));
    setTicked((t) => {
      const kept = new Set([...t].filter((id) => shown.has(id)));
      return kept.size === t.size ? t : kept;
    });
  }, [annotations, status]);

  const allTicked = rows.length > 0 && rows.every((f) => ticked.has(f.id));
  const toggle = (id) => setTicked((t) => {
    const next = new Set(t);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const bulk = async (action) => {
    if (await onBulk([...ticked], action)) setTicked(new Set());
  };

  return (
    <section>
      <p className="explain">Every outline on this image. Click a row to open it on the
        map. ✕ rejects it (kept as a training example), 🗑 deletes it for good. Tick rows
        to accept, reject or delete several at once.</p>
      <div className="row between">
        <h2>Outlines ({rows.length})</h2>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {["all", "proposed", "accepted", "rejected"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
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
      <table className="table">
        <thead>
          <tr>
            <th>
              <input type="checkbox" checked={allTicked} disabled={!rows.length}
                     title="Tick every row shown"
                     onChange={() => setTicked(allTicked ? new Set() : new Set(rows.map((f) => f.id)))} />
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
    </section>
  );
}

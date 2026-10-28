// "Complete for" checkboxes: tick a label once every object of it in this
// image is outlined. Training only uses images complete for a label.
import { useState } from "react";

export default function CompletePanel({ imageLabels, allLabels, onToggle }) {
  const [extra, setExtra] = useState("");
  const shown = new Map(imageLabels.map((l) => [l.label, l]));
  for (const l of allLabels) {
    if (!shown.has(l.label)) shown.set(l.label, { label: l.label, complete: false, accepted: 0 });
  }
  const rows = [...shown.values()].sort((a, b) => a.label.localeCompare(b.label));
  return (
    <section>
      <h2 title="Tick a label when every object of it in this image is outlined">
        Complete for this image
      </h2>
      <p className="explain">Tick a label once every one of them in this image is outlined
        (or there are none). Only complete images are used for training.</p>
      {rows.length === 0 && <p className="muted small">No labels yet.</p>}
      <ul className="plain checks">
        {rows.map((l) => (
          <li key={l.label}>
            <label className="check">
              <input type="checkbox" checked={l.complete}
                     onChange={(e) => onToggle(l.label, e.target.checked)} />
              {l.label} <span className="muted">({l.accepted ?? 0} saved)</span>
            </label>
          </li>
        ))}
      </ul>
      <form className="row" onSubmit={(e) => {
        e.preventDefault();
        if (extra.trim()) onToggle(extra.trim(), true);
        setExtra("");
      }}>
        <input value={extra} onChange={(e) => setExtra(e.target.value)}
               placeholder="label not in this image" title="Mark complete for a label with no objects here" />
        <button type="submit" disabled={!extra.trim()}>Mark</button>
      </form>
    </section>
  );
}

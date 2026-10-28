// All labels in use, with a rename (or merge) action.
import { labelColor } from "../layers.js";

export default function LabelsPanel({ labels, hiddenLabels, onToggleLabel, onRename }) {
  return (
    <section>
      <p className="explain">Untick to hide a label on the map. Rename fixes a label
        everywhere; renaming onto an existing label merges them.</p>
      {labels.length === 0 && <p className="muted small">No labels yet.</p>}
      <ul className="plain">
        {labels.map((l) => (
          <li key={l.label} className="label-row">
            <label className="check">
              <input type="checkbox" checked={!hiddenLabels.includes(l.label)}
                     onChange={() => onToggleLabel(l.label)} />
              <span className="swatch" style={{ background: labelColor(l.label) }} />
              {l.label}
            </label>
            <span className="muted small">{l.accepted} · {l.images} img</span>
            <button className="small-button" onClick={() => onRename(l.label)}
                    title={`Rename “${l.label}” on every image`}>Rename</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

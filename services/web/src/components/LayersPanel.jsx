// Show or hide each kind of thing drawn on the map.
const LAYERS = [
  ["saved", "Saved outlines", "Outlines you have saved"],
  ["suggestions", "Suggestions", "Unreviewed outlines from Find similar and sweeps"],
  ["areas", "Labeled areas", "Rectangles marked on the Review tab"],
];

export default function LayersPanel({ visibility, onChange, showLabels, onShowLabels,
                                      showCorners, onShowCorners }) {
  return (
    <section>
      {LAYERS.map(([key, text, help]) => (
        <label key={key} className="check" title={help}>
          <input type="checkbox" checked={visibility[key]}
                 onChange={(e) => onChange({ ...visibility, [key]: e.target.checked })} />
          {text}
        </label>
      ))}
      <label className="check" title="Write each outline's label on the map">
        <input type="checkbox" checked={showLabels} onChange={(e) => onShowLabels(e.target.checked)} />
        Label names
      </label>
      <label className="check" title="Corner dots on the outline being edited (shortcut: V)">
        <input type="checkbox" checked={showCorners} onChange={(e) => onShowCorners(e.target.checked)} />
        Corner dots while editing
      </label>
    </section>
  );
}

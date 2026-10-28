// The "?" box: the workflow and every shortcut in one place.
const SHORTCUTS = [
  ["1", "Pan / select: drag to move around, click an outline to open it"],
  ["2", "Box: drag a rectangle; SAM outlines the object inside"],
  ["3", "Click: click an object; SAM outlines it"],
  ["4", "Draw: draw an outline by hand (double-click to finish)"],
  ["5", "Area: drag a rectangle to mark a labeled area (Review tab)"],
  ["V", "While editing: hide / show the corner dots"],
  ["E", "While editing: edit corners"],
  ["Shift+click", "Select a second corner; the stretch between them turns orange"],
  ["S", "Straighten: replace the orange corners with a straight line"],
  ["X", "While editing: Erase (draw a shape to cut out)"],
  ["A", "While editing: Add (draw a shape to merge in)"],
  ["F", "While editing: refine with clicks (Alt+click excludes)"],
  ["H", "While editing: fill holes on/off"],
  ["Delete", "Remove the selected corner, hole, or orange stretch (Alt+click also removes a corner)"],
  ["Ctrl+Z", "Undo the last corner edit"],
  ["Enter", "Save the outline with its label"],
  ["Esc", "Cancel / close the outline"],
  ["N / P", "Suggestions tab: next / previous suggestion"],
  ["A / X / D", "Suggestions tab: accept / reject / delete the current suggestion"],
  ["?", "Show this help"],
];

export default function HelpDialog({ onClose }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between">
          <h1>How to use the Segmentation Assistant</h1>
          <button onClick={onClose}>Close</button>
        </div>
        <ol className="steps">
          <li><b>Pick an image</b> on the left (upload one, or put files in data/images/ and
            press Rescan).</li>
          <li><b>Look around</b> with <b>Pan / select</b> (drag to move, scroll to zoom);
            click any outline to open it.</li>
          <li><b>Mark one object.</b> Use <b>Box</b> (best for small fields or blurry
            imagery), <b>Click</b>, or <b>Draw</b> on the Outline tab. With Box you can type
            what to <b>look for</b> (e.g. "pepper field"): SAM then finds every one inside the
            box; the best opens for editing and the rest become suggestions.</li>
          <li><b>Adjust the outline.</b> Corners are white dots: drag one to move it, click an
            edge to add one, click one and press Delete to remove it. To flatten a wobbly edge
            or cut off a wrong lobe, click the corner on one side, Shift+click the corner on
            the other side, and press Straighten (S). Click inside a hole and press Delete to
            fill it. Erase and Add cut out or merge in a shape you draw. Simplify drops
            corners that are nearly in line. Fill holes and smoothing redo the outline from
            SAM's original. Refine with clicks lets SAM try again.</li>
          <li><b>Label and save.</b> Type or pick a label and press Save (Enter). SAM's
            original outline is kept next to your version. Discard and Reject keep a wrong
            outline as a training example; Delete removes it for good.</li>
          <li><b>Mark the image complete</b> for a label (Review tab) once every one of them
            is outlined. Training only uses complete images.</li>
        </ol>
        <p className="muted small">Other tabs: <b>Suggestions</b> finds similar objects for you
          to accept, reject or delete, one by one or all at once; <b>Sweep</b> searches the
          whole image for a label; <b>Review</b> marks what is fully outlined, for the whole
          image or a rectangle, and reviews those rectangles; <b>Table</b> lists every outline,
          with bulk accept, reject and delete. Changing tabs returns the map to Pan / select.
          “Start over on this image” (left) deletes everything outlined on it. Left sections
          fold away by clicking their heading.</p>
        <h2>Shortcuts</h2>
        <table className="table">
          <tbody>
            {SHORTCUTS.map(([key, text]) => (
              <tr key={key}><td><kbd>{key}</kbd></td><td>{text}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

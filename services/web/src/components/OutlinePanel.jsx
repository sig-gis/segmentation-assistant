// Outline one object at a time: pick a tool, get an outline, adjust it, label
// it, save it.
import { cornerCount, holeCount } from "../draft.js";

const TOOLS = [
  ["pan", "Pan / select", "1", "Drag to move around. Click an outline to open it and change its shape or label."],
  ["box", "Box", "2", "Drag a rectangle around one object. SAM only looks inside it."],
  ["click", "Click", "3", "Click one object. Works best on sharp imagery."],
  ["draw", "Draw", "4", "Draw the outline yourself. Double-click to finish."],
];

function percent(value) {
  return value == null ? "–" : `${Math.round(value * 100)}%`;
}

export default function OutlinePanel({
  mode, onMode, modelReady,
  draft, draftInfo, draftTool, onDraftTool,
  label, onLabel, labels,
  onFillHoles, onSmoothing, onUndo, onDeleteCorner, onSave, onCancel, onRemove, onDelete,
  removingCount, onStraighten, onFlip, onSimplify, onRemoveHole,
  lookFor, onLookFor, showCorners, onShowCorners,
}) {
  if (!draft) {
    return (
      <section>
        <p className="explain">
          Outline one object at a time. Pick a tool, mark the object on the map, then adjust
          the outline and save it with a label.
        </p>
        <h2>Tool</h2>
        <div className="tool-list">
          {TOOLS.map(([value, text, key, help]) => (
            <button key={value} className={`tool ${mode === value ? "active" : ""}`}
                    onClick={() => onMode(value)} title={`Shortcut: ${key}`}>
              <b>{text}</b> <kbd>{key}</kbd>
              <span>{help}</span>
            </button>
          ))}
        </div>
        {mode === "box" && (
          <label className="field" title="Leave empty to outline the main object in the box">
            Look for (optional)
            <input value={lookFor} onChange={(e) => onLookFor(e.target.value)}
                   placeholder="e.g. pepper field" />
            <span className="muted small">
              {lookFor.trim()
                ? `SAM finds every “${lookFor.trim()}” inside the box; the best opens for editing, the rest become suggestions.`
                : "Empty: SAM outlines the main object in the box."}
            </span>
          </label>
        )}
        {!modelReady && (mode === "box" || mode === "click") && (
          <p className="warn small">The model is still loading; Box and Click work once the
            status at the top says ready.</p>
        )}
      </section>
    );
  }

  const fromModel = Boolean(draftInfo?.hasOriginal);
  return (
    <section>
      <p className="explain">
        {draft.kind === "saved" ? "Editing a saved outline." : "Adjust the outline, then save it."}
        {" "}Drag a white corner to move it, click an edge to add a corner, click a corner and
        press Delete to remove it. Shift+click a second corner to straighten everything
        between them. Click inside a hole to select it.
      </p>

      <label className="field">
        Label
        <input list="label-options" value={label} onChange={(e) => onLabel(e.target.value)}
               placeholder="e.g. field" autoFocus />
        <datalist id="label-options">
          {labels.map((l) => <option key={l.label} value={l.label} />)}
        </datalist>
      </label>

      <div className="box">
        <div className="row">
          <button className={draftTool === "corners" ? "active" : ""}
                  onClick={() => onDraftTool("corners")}
                  title="Drag, add and remove corners (shortcut: E)">Edit corners</button>
          <button className={draftTool === "erase" ? "active" : ""}
                  onClick={() => onDraftTool("erase")}
                  title="Draw a shape over a wrong part; it is cut out (shortcut: X)">Erase</button>
          <button className={draftTool === "add" ? "active" : ""}
                  onClick={() => onDraftTool("add")}
                  title="Draw a shape over a missing part; it is added (shortcut: A)">Add</button>
          <button className={draftTool === "refine" ? "active" : ""}
                  onClick={() => onDraftTool("refine")} disabled={!modelReady}
                  title="Click inside to include, Alt+click to exclude; SAM redraws (shortcut: F)">
            Refine with clicks
          </button>
        </div>
        {(draftTool === "erase" || draftTool === "add") && (
          <p className="muted small">Click around the part to {draftTool === "erase"
            ? "cut out" : "add"}, double-click to finish. Ctrl+Z undoes it.</p>
        )}
        {draftTool === "refine" && (
          <p className="muted small">Click to include an area, Alt+click to exclude one. SAM
            redraws the outline (your corner edits are replaced).
            {draft.kind === "saved" && " When you save, the new outline replaces the saved one."}
          </p>
        )}
        {!modelReady && <p className="warn small">Refine needs the model; it is still loading.</p>}
        <div className="row">
          <button onClick={onUndo} disabled={!draft.history.length} title="Ctrl+Z">Undo</button>
          <button className={showCorners ? "" : "active"} onClick={() => onShowCorners(!showCorners)}
                  title="Hide the corner dots to see the outline against the image (shortcut: V)">
            {showCorners ? "Hide corners" : "Show corners"}
          </button>
          <button onClick={onDeleteCorner} disabled={!draft.selectedVertex || draft.rangeEnd}
                  title="Delete key">Delete corner</button>
          {draft.selectedHole && (
            <button onClick={onRemoveHole} title="Delete key">Remove hole</button>
          )}
        </div>
        {draft.rangeEnd && (
          <div className="row">
            <button className="primary" onClick={onStraighten} disabled={!removingCount}
                    title="Replace the orange corners with a straight line (shortcut: S)">
              Straighten ({removingCount} corners)
            </button>
            <button onClick={onFlip} title="Straighten the other way round instead">
              Flip side
            </button>
          </div>
        )}
        <div className="row">
          <span className="muted small grow-left"
                title="Drop corners that are nearly in line. Always computed from the shape before simplifying, so you can go back.">
            Simplify
          </span>
          {[0, 1, 2, 4].map((px) => (
            <button key={px}
                    className={`small-button ${draft.simplifyLevel === px ? "active" : ""}`}
                    onClick={() => onSimplify(px)}
                    title={px ? `Drop corners within ${px} pixel(s) of a straight line` : "No simplifying"}>
              {px ? `${px} px` : "off"}
            </button>
          ))}
        </div>
        <p className="muted small">
          {cornerCount(draft.geometry)} corners · {holeCount(draft.geometry)} holes
          {draft.rangeEnd ? " · orange corners will be removed" : ""}
        </p>
      </div>

      {fromModel && (
        <div className="box">
          <label className="check" title="Fill every hole inside the outline (shortcut: H)">
            <input type="checkbox" checked={draft.cleanup.fill_holes}
                   onChange={(e) => onFillHoles(e.target.checked)} />
            Fill holes
          </label>
          <label className="check" title="Round off sharp corners">
            Smoothing
            <select value={draft.cleanup.smoothing} onChange={(e) => onSmoothing(e.target.value)}>
              <option value="off">off</option>
              <option value="light">light</option>
              <option value="strong">strong</option>
            </select>
          </label>
          <p className="muted small">These redo the outline from SAM's original; corner edits
            are replaced.</p>
        </div>
      )}

      {draftInfo && (
        <dl className="details">
          <dt>Model confidence</dt><dd>{percent(draftInfo.score)}</dd>
          {draft.kind === "saved" && <><dt>Unchanged from model</dt><dd>{percent(draftInfo.edit_iou)}</dd></>}
        </dl>
      )}

      <div className="row">
        <button className="primary" onClick={onSave} disabled={!label.trim()} title="Enter">
          Save as “{label || "…"}”
        </button>
        <button onClick={onCancel}
                title={draft.kind === "model"
                  ? "Close without saving; kept as a rejected example for training (Esc)"
                  : "Close without saving changes (Esc)"}>
          {draft.kind === "model" ? "Discard" : "Close"}
        </button>
      </div>
      <div className="row">
        {draft.kind !== "model" && (
          <button onClick={onRemove}
                  title="Mark it wrong but keep it as a rejected example for training">
            Reject (keep as example)
          </button>
        )}
        <button className="danger" onClick={onDelete} title="Delete this outline for good">
          Delete
        </button>
      </div>
    </section>
  );
}

// Suggestions: let SAM find objects like the current label or like a saved
// outline, then go through them one at a time or all at once.
import { ORIGINS } from "../review.js";

export default function SuggestionsPanel({
  label, exampleId, search, onSearch, onFindSimilar, modelReady,
  queue, currentIndex, filters, onFilters, onStep, onAccept, onReject, onDelete, onEdit, onBulk,
}) {
  const withExample = search.useExample && Boolean(exampleId);
  const byName = !withExample || search.useName;
  return (
    <section>
      <p className="explain">
        SAM looks for every “{label || "…"}” in the image. Each result is a suggestion: accept
        it, reject it, or open it to fix the shape. Suggestions show on the map in dashed
        yellow while this tab is open.
      </p>
      <h2>Find similar</h2>
      <div className="box">
        <label className="check"
               title="Open a saved outline first (Pan / select, click it) to use it as the example">
          <input type="checkbox" checked={withExample}
                 disabled={!exampleId} onChange={(e) => onSearch({ ...search, useExample: e.target.checked })} />
          Look for things that look like the open saved outline
        </label>
        {withExample && (
          <label className="check"
                 title="SAM also matches the label's name. Unusual names can make it find less.">
            <input type="checkbox" checked={search.useName}
                   onChange={(e) => onSearch({ ...search, useName: e.target.checked })} />
            Also match the name “{label || "…"}”
          </label>
        )}
        {!exampleId && (
          <p className="muted small">Without an example, SAM searches by the label's name only.</p>
        )}
        <label className="check" title="Lower finds more, including more wrong ones">
          Keep matches above {Math.round(search.minScore * 100)}%
          <input type="range" min="0.05" max="0.9" step="0.05" value={search.minScore}
                 onChange={(e) => onSearch({ ...search, minScore: Number(e.target.value) })} />
        </label>
        <button className="primary" onClick={() => onFindSimilar({ withExample, byName })}
                disabled={!modelReady || !label.trim()}
                title="Search the whole image. Outlines already on the image are not suggested again.">
          Find more “{label || "…"}”
        </button>
      </div>

      <h2>Suggestions to review</h2>
      <div className="row">
        <select value={filters.origin} title="Where the suggestions came from"
                onChange={(e) => onFilters({ ...filters, origin: e.target.value, jobId: null })}>
          {Object.entries(ORIGINS).map(([value, text]) => (
            <option key={value} value={value}>{text}</option>
          ))}
        </select>
        <label className="check" title="Hide suggestions the model is less sure about">
          Min confidence {Math.round(filters.minScore * 100)}%
          <input type="range" min="0" max="1" step="0.05" value={filters.minScore}
                 onChange={(e) => onFilters({ ...filters, minScore: Number(e.target.value) })} />
        </label>
      </div>
      {filters.jobId && (
        <p className="small">
          One sweep only.{" "}
          <button className="small-button" onClick={() => onFilters({ ...filters, jobId: null })}>
            Show all
          </button>
        </p>
      )}
      {queue.length === 0 ? (
        <p className="muted small">Nothing to review.</p>
      ) : (
        <div className="box">
          <div className="row">
            <button onClick={() => onStep(-1)} title="Shortcut: P">Previous</button>
            <span className="muted small grow">
              {currentIndex >= 0 ? `${currentIndex + 1} of ${queue.length}` : `${queue.length} waiting`}
            </span>
            <button onClick={() => onStep(1)} title="Shortcut: N">Next</button>
          </div>
          <div className="row">
            <button className="primary" onClick={onAccept} disabled={currentIndex < 0}
                    title="Save it as the current label (shortcut: A)">Accept as “{label}”</button>
            <button className="danger" onClick={onReject} disabled={currentIndex < 0}
                    title="Shortcut: X">Reject</button>
            <button onClick={onEdit} disabled={currentIndex < 0}
                    title="Open it on the Outline tab to fix the shape">Fix shape</button>
            <button className="danger" onClick={onDelete} disabled={currentIndex < 0}
                    title="Delete for good, not kept as an example (shortcut: D)">Delete</button>
          </div>
          <p className="muted small">Every suggestion in this list ({queue.length}):</p>
          <div className="row">
            <button onClick={() => onBulk("accept")} title={`Accept all as “${label}”`}>
              Accept all
            </button>
            <button onClick={() => onBulk("reject")} title="Reject all (kept as training examples)">
              Reject all
            </button>
            <button className="danger" onClick={() => onBulk("delete")} title="Delete all for good">
              Delete all
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

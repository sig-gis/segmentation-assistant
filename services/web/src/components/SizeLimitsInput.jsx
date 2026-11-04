// Smallest and largest object to keep, as the side of a square in metres
// (or pixels when the image has no size in metres). Shown on the map at
// true scale while set.
import { numberOrEmpty } from "../inputs.js";

export default function SizeLimitsInput({ limits, onChange, unit }) {
  return (
    <div>
      <div className="row">
        <label className="field inline" title="Leave empty for no lower limit">
          Smallest ({unit})
          <input type="number" min="0" value={limits.min}
                 onChange={(e) => onChange({ ...limits, min: numberOrEmpty(e.target.value) })} />
        </label>
        <label className="field inline" title="Leave empty for no upper limit">
          Largest ({unit})
          <input type="number" min="0" value={limits.max}
                 onChange={(e) => onChange({ ...limits, max: numberOrEmpty(e.target.value) })} />
        </label>
      </div>
      <p className="muted small">
        Side of a square; objects of a smaller or larger area are left out. The map shows
        each size as a square and a circle of the same area.
      </p>
    </div>
  );
}

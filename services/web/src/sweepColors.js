// A color per sweep, so several sweeps on one image can be told apart. The
// default comes from the sweep id (the same after a reload); a color the
// user picks is kept in this browser only (`overrides`: { jobId: color }).
// Yellow is left out: it marks unreviewed suggestions.

export const SWEEP_PALETTE = ["#e64980", "#7048e8", "#1098ad", "#e8590c",
                              "#2f9e44", "#4263eb", "#c2255c", "#0c8599"];

export function sweepColor(jobId, overrides) {
  if (overrides?.[jobId]) return overrides[jobId];
  let hash = 0;
  for (const ch of jobId ?? "") hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return SWEEP_PALETTE[hash % SWEEP_PALETTE.length];
}

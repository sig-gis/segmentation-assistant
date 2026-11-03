// What the map shows. Saved outlines and suggestions are separate layers
// that can be hidden, and individual labels can be hidden too.

export const defaultVisibility = {
  saved: true,
  suggestions: false,
  areas: true,
  tiles: false,
  hiddenLabels: [],
};

export function isSaved(properties) {
  return properties.status === "accepted";
}

export function isSuggestion(properties) {
  return properties.status === "proposed";
}

// features to draw on the saved and suggestion layers (never the draft,
// which has its own editable layer, nor an outline it is replacing).
// `skipIds` is one id or a list.
export function visibleFeatures(collection, visibility, skipIds) {
  const hidden = new Set(visibility.hiddenLabels);
  const skip = new Set([].concat(skipIds ?? []));
  const shown = (f) => !skip.has(f.id) && !hidden.has(f.properties.label);
  const features = collection?.features ?? [];
  return {
    saved: visibility.saved ? features.filter((f) => isSaved(f.properties) && shown(f)) : [],
    suggestions: visibility.suggestions
      ? features.filter((f) => isSuggestion(f.properties) && shown(f))
      : [],
  };
}

export function toggleLabel(visibility, label) {
  const hidden = new Set(visibility.hiddenLabels);
  hidden.has(label) ? hidden.delete(label) : hidden.add(label);
  return { ...visibility, hiddenLabels: [...hidden] };
}

// a stable color per label, so the same label looks the same everywhere
const PALETTE = ["#2bb24c", "#2f7dff", "#e8590c", "#ae3ec9", "#0ca678", "#f08c00",
                 "#e64980", "#1098ad", "#74b816", "#7048e8"];

export function labelColor(label) {
  let hash = 0;
  for (const ch of label ?? "") hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

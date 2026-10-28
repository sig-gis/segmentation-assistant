// The review queue: unreviewed proposals for one image, filtered and sorted
// most confident first.

// Where a proposal came from, from its prompt type / sweep id.
export function origin(properties) {
  if (properties.job_id) return "sweep";
  const type = properties.prompt_type;
  if (type === "point" || type === "box") return "click";
  if (type === "text" || type === "exemplar" || type === "text+exemplar") return "similar";
  return "other";
}

export const ORIGINS = {
  all: "All",
  click: "Clicks",
  similar: "Find similar",
  sweep: "Sweeps",
};

export function reviewQueue(
  collection,
  { origin: wanted = "all", minScore = 0, jobId = null } = {}
) {
  if (!collection) return [];
  return collection.features
    .filter((f) => f.properties.status === "proposed")
    .filter((f) => wanted === "all" || origin(f.properties) === wanted)
    .filter((f) => !jobId || f.properties.job_id === jobId)
    // sweeps have no score; a score filter must not hide them
    .filter((f) => f.properties.score == null || f.properties.score >= minScore)
    .sort((a, b) => (b.properties.score ?? -1) - (a.properties.score ?? -1))
    .map((f) => f.id);
}

// The item to show after `currentId` is reviewed or skipped.
export function nextId(queue, currentId, step = 1) {
  if (!queue.length) return null;
  const index = queue.indexOf(currentId);
  if (index === -1) return queue[0];
  return queue[(index + step + queue.length) % queue.length];
}

// Sweeps that produced outlines on this image, with their outline ids by
// status (`ids.proposed`, ...) and counts.
export function sweepsIn(collection) {
  const sweeps = new Map();
  for (const f of collection?.features ?? []) {
    const id = f.properties.job_id;
    if (!id) continue;
    const entry = sweeps.get(id) ?? { jobId: id, label: f.properties.label,
                                      proposed: 0, accepted: 0, rejected: 0,
                                      ids: { proposed: [], accepted: [], rejected: [] } };
    entry[f.properties.status] += 1;
    entry.ids[f.properties.status].push(f.id);
    sweeps.set(id, entry);
  }
  return [...sweeps.values()];
}

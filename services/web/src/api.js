// Thin client for the segmentation API. Every call goes to /api/..., which
// nginx (Docker) or the Vite dev server forwards to the api service.

const BASE = "/api";

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
  }
}

async function request(path, { method = "GET", body, query } = {}) {
  let url = `${BASE}${path}`;
  if (query) {
    const params = new URLSearchParams(
      Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== "")
    );
    if ([...params].length) url += `?${params}`;
  }
  const options = { method, headers: {} };
  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  if (response.status === 204) return null;
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new ApiError(response.status, data?.detail ?? text);
  return data;
}

export const api = {
  health: () => request("/healthz"),

  listImages: () => request("/images"),
  scanImages: () => request("/images/scan", { method: "POST" }),
  uploadImage: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/images/upload", { method: "POST", body: form });
  },
  updateImage: (id, fields) => request(`/images/${id}`, { method: "PUT", body: fields }),
  // delete every outline, area and complete mark on one image; the image stays
  resetImage: (id) => request(`/images/${id}/reset`, { method: "POST" }),
  displayUrl: (imageId) => `${BASE}/images/${imageId}/display`,
  // the tiles a sweep would use; size/overlap default to the API settings
  imageTiles: (imageId, size, overlap) =>
    request(`/images/${imageId}/tiles`, { query: { size, overlap } }),

  annotationsGeojson: (imageId) =>
    request("/annotations/geojson", { query: { image_id: imageId, crs: "image" } }),
  updateAnnotation: (id, fields) => request(`/annotations/${id}`, { method: "PUT", body: fields }),
  createAnnotation: (body) => request("/annotations", { method: "POST", body }),
  // action: accept | reject | delete; label (accept only) relabels them all
  bulkAnnotations: (ids, action, label = null) =>
    request("/annotations/bulk", {
      method: "POST",
      body: { annotation_ids: ids, action, label },
    }),

  // text and/or accepted outlines used as examples -> every similar object
  // region [xmin, ymin, xmax, ymax] in image coordinates: search only there
  // minScore: lowest match score kept; skipExisting: leave out repeats of
  // outlines already on the image
  // minSize / maxSize: smallest and largest area kept, in image pixels²
  findSimilar: ({ imageId, text, exampleIds = [], cleanup, region = null, minScore = null,
                  skipExisting = false, minSize = null, maxSize = null }) =>
    request("/segment/concept", {
      method: "POST",
      body: {
        image_id: imageId,
        text: text || null,
        exemplars: exampleIds.map((id) => ({ annotation_id: id })),
        cleanup,
        region,
        coord_space: "image",
        min_score: minScore,
        skip_existing: skipExisting,
        min_size: minSize,
        max_size: maxSize,
      },
    }),

  // exampleId: a saved outline that guides the tile it is in
  startSweep: ({ imageId, text, tileSize, overlap, exampleId = null, minSize = null,
                 maxSize = null }) =>
    request("/segment/tiled", {
      method: "POST",
      body: {
        image_id: imageId, text, tile_size: tileSize, overlap,
        exemplars: exampleId ? [{ annotation_id: exampleId }] : [],
        min_size: minSize, max_size: maxSize,
      },
    }),
  getJob: (jobId) => request(`/segment/jobs/${jobId}`),
  // an image's sweeps, newest first, with their settings
  listJobs: (imageId) => request("/segment/jobs", { query: { image_id: imageId } }),

  listAreas: ({ imageId, status } = {}) =>
    request("/areas", { query: { image_id: imageId, status } }),
  createArea: ({ imageId, label, geometry }) =>
    request("/areas", { method: "POST", body: { image_id: imageId, label, geometry } }),
  // action: submit | approve | return | reopen
  areaAction: (areaId, action, note) =>
    request(`/areas/${areaId}`, { method: "PUT", body: { action, note: note ?? null } }),
  deleteArea: (areaId) => request(`/areas/${areaId}`, { method: "DELETE" }),

  // box [xmin, ymin, xmax, ymax] and/or points [{x, y, label}] in the image's
  // own coordinates (point label 1 = include, 0 = exclude)
  segment: ({ imageId, points = [], box = null, label, priorId = null, cleanup }) =>
    request("/segment/interactive", {
      method: "POST",
      body: {
        image_id: imageId,
        points,
        box,
        label,
        coord_space: "image",
        prior_annotation_id: priorId,
        cleanup,
      },
    }),
  getAnnotation: (id) => request(`/annotations/${id}`),
  // gone for good; use updateAnnotation(id, {status: "rejected"}) to keep it as an example
  deleteAnnotation: (id) => request(`/annotations/${id}`, { method: "DELETE" }),
  recleanAnnotation: (id, cleanup) =>
    request(`/annotations/${id}/clean`, { method: "POST", body: cleanup }),

  imageLabels: (imageId) => request(`/images/${imageId}/labels`),
  setImageLabel: (imageId, label, complete) =>
    request(`/images/${imageId}/labels/${encodeURIComponent(label)}`, {
      method: "PUT",
      body: { complete },
    }),
  listLabels: () => request("/labels"),
  renameLabel: ({ old, new: next, imageId = null }) =>
    request("/labels/rename", { method: "POST", body: { old, new: next, image_id: imageId } }),
};

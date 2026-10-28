-- The whole schema. Applied by the API on first start against an empty
-- database (services/api/app/migrations.py); later changes go in 002_*.sql,
-- 003_*.sql, ...

CREATE EXTENSION IF NOT EXISTS postgis;

-- One row per image file. Pixels stay on disk; this records facts about them.
CREATE TABLE images (
    image_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_uri  TEXT NOT NULL,           -- path under /data
    kind        TEXT NOT NULL CHECK (kind IN ('geotiff', 'png')),
    crs         TEXT,                    -- e.g. 'EPSG:32648'; NULL when not georeferenced.
                                         -- Never invented: a PNG has no CRS.
    width       INTEGER,
    height      INTEGER,
    checksum    TEXT,
    -- where the image came from
    provider    TEXT,                    -- e.g. 'Sentinel-2 (Earth Search)'
    acquired_at DATE,                    -- capture date
    license     TEXT,
    source_ref  TEXT,                    -- the provider's own id, e.g. a scene id
    created_at  TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT png_has_no_crs CHECK (kind <> 'png' OR crs IS NULL)
);

-- EPSG code of an image's CRS ('EPSG:32648' -> 32648); 0 when the image has no
-- CRS (PNG) or one that is not a plain EPSG code. Geometries are stored with it.
CREATE FUNCTION image_srid(_image_id uuid) RETURNS integer AS $$
    SELECT COALESCE(
        (SELECT CASE WHEN crs ~ '^EPSG:[0-9]+$' THEN split_part(crs, ':', 2)::integer
                     ELSE 0 END
         FROM images WHERE image_id = _image_id),
        0)
$$ LANGUAGE sql STABLE;

-- One row per outline. Geometries are in the image's CRS (SRID from
-- image_srid), or pixels with SRID 0 for PNGs.
CREATE TABLE annotations (
    annotation_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_id        UUID NOT NULL REFERENCES images(image_id) ON DELETE CASCADE,
    geom            geometry(MultiPolygon),    -- current outline
    original_geom   geometry(MultiPolygon),    -- the model's outline, never edited
    label           TEXT NOT NULL,             -- e.g. 'field'
    source          TEXT NOT NULL CHECK (source IN ('model', 'human_correction', 'human_new')),
    -- how the current outline was made
    edit_method     TEXT CHECK (edit_method IN ('model', 'refine_click', 'vertex_edit', 'drawn')),
    edit_iou        REAL,                      -- overlap of original vs current, 0..1
    -- {type: point|box|text|exemplar|..., value, backend, model, job_id, ...}
    prompt          JSONB,
    score           REAL,
    mask_raster_uri TEXT,                      -- optional mask raster file
    logits_uri      TEXT,                      -- saved low-res mask (.npy) for click refinement
    status          TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed', 'accepted', 'rejected')),
    split           TEXT DEFAULT 'unassigned'
                    CHECK (split IN ('train', 'val', 'test', 'unassigned')),
    created_by      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ
);
CREATE INDEX annotations_image_id ON annotations(image_id);
CREATE INDEX annotations_geom_gist ON annotations USING GIST(geom);
CREATE INDEX annotations_status ON annotations(status);

-- A region of one image where every object of `label` is outlined. Only
-- approved areas become training data.
CREATE TABLE labeled_areas (
    area_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_id      UUID NOT NULL REFERENCES images(image_id) ON DELETE CASCADE,
    geom          geometry(Polygon) NOT NULL,   -- image CRS (SRID set), pixels for PNG
    label         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'submitted', 'approved', 'returned')),
    note          TEXT,                         -- reviewer's reason when returned
    created_at    TIMESTAMPTZ DEFAULT now(),
    submitted_at  TIMESTAMPTZ,
    reviewed_at   TIMESTAMPTZ
);
CREATE INDEX labeled_areas_image_id ON labeled_areas(image_id);
CREATE INDEX labeled_areas_geom_gist ON labeled_areas USING GIST(geom);

-- Which labels each image is complete for: every object of that label in the
-- image is outlined, so the rest of the image can be taught as "not this label".
CREATE TABLE image_labels (
    image_id      UUID NOT NULL REFERENCES images(image_id) ON DELETE CASCADE,
    label         TEXT NOT NULL,
    complete      BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at  TIMESTAMPTZ,
    PRIMARY KEY (image_id, label)
);
CREATE INDEX image_labels_label ON image_labels(label);

-- Long-running inference work (full-image sweeps).
CREATE TABLE jobs (
    job_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         TEXT NOT NULL CHECK (kind IN ('tiled_segmentation')),
    image_id     UUID REFERENCES images(image_id) ON DELETE SET NULL,
    params       JSONB NOT NULL,               -- text, tile_size, overlap, ...
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    result       JSONB,                        -- output files, annotation count
    error        TEXT,
    created_at   TIMESTAMPTZ DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ
);
CREATE INDEX jobs_status ON jobs(status);

-- One row per fine-tune attempt.
CREATE TABLE training_runs (
    run_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at        TIMESTAMPTZ DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    status            TEXT CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    backend           TEXT CHECK (backend IN ('local', 'slurm')),
    hf_checkpoint_ref TEXT,                    -- published adapter, repo@revision
    dataset_snapshot  JSONB,                   -- annotation ids / split counts used
    metrics           JSONB                    -- held-out base-vs-adapter results
);

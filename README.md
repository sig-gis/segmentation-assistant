# segmentation-service

A SAM-3 segmentation service for remote-sensing imagery, built for Collect
Earth Online. Users outline objects on a map with SAM-3's help and correct
what it proposes. Every proposal and correction is saved to PostGIS, and the
accepted outlines are later used to fine-tune SAM-3 (LoRA adapters on a
frozen model).

## Layout

```
docker-compose.yml       storage (PostGIS) + api (GPU) + web (map UI) + finetune (profile)
pyproject.toml, uv.lock  uv workspace for local Python tools and tests
config/settings.yaml     tunables: model/checkpoint choice, tile size, outline cleanup
.env.example             secrets: DB password/DSN, HF_TOKEN, SLURM_* (copy to .env)
services/
  api/                   FastAPI backend (tests: services/api/tests)
  storage/migrations/    PostGIS schema, applied by the api at startup
  web/                   React + OpenLayers map UI (nginx; /api is forwarded to the api)
  finetune/              seg-finetune CLI (tests: services/finetune/tests)
vendor/sam3/             git submodule: facebookresearch/sam3, pinned commit
scripts/                 download_sentinel2.py: free Sentinel-2 test images
data/                    runtime folder, mounted into the containers at /data
  images/                source imagery; files here register themselves
  logits/                saved low-res masks, for click refinement
  display/               browser-ready copies of GeoTIFFs (rebuilt if deleted)
  crops/                 cut-outs around box prompts (rebuilt if deleted)
  outputs/               tiled job results, one folder per job
```

## Running it locally

### Requirements
- A system with an NVIDIA GPU (~4 GB VRAM should be sufficient, a single consumer GPU is enough for inference).
- Docker with Docker Compose, and the
  [NVIDIA container toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/).
- A Hugging Face account that has accepted the license for the gated
  [facebook/sam3](https://huggingface.co/facebook/sam3) weights, and an access
  token from https://huggingface.co/settings/tokens.
- [uv](https://docs.astral.sh/uv/) for the tests and the Sentinel-2 download
  script. Node.js and npm for the map UI tests.

### One-time setup
```bash
git clone --recursive https://github.com/sig-gis/segmentation-assistant.git   # --recursive also fetches vendor/sam3
cd segmentation-assistant
# cloned without --recursive? run: git submodule update --init
cp .env.example .env
mkdir -p data/images
```
In `.env`, set `POSTGRES_PASSWORD`, put the same password in `DATABASE_URL`,
and set `HF_TOKEN`. Keep `storage` as the host in `DATABASE_URL`; it is the
database's name inside Docker Compose.

### Start
```bash
docker compose up -d storage api web
curl localhost:8000/healthz     # {"status":"loading"} until the model is on the GPU, then {"status":"ready"}
```
The first start builds the images, creates the database schema and downloads
the SAM-3 weights (kept in the `hfcache` volume afterwards), so it takes a
few minutes.

| Service | Address |
|---|---|
| Map UI | http://localhost:8081 |
| API | http://localhost:8000 (reference at http://localhost:8000/docs) |
| PostGIS | `localhost:5432` |

Stop everything with `docker compose down`. The database and the downloaded
weights are kept in Docker volumes (`pgdata`, `hfcache`).

### Adding images
- Drop a GeoTIFF or PNG into `data/images/`. It appears in the UI on the next
  page load or after "Rescan folder".
- Or use "Upload…" in the UI.
- Or download free Sentinel-2 test images:
  ```bash
  uv run python scripts/download_sentinel2.py --list   # show available areas
  uv run python scripts/download_sentinel2.py --all
  ```

An optional `<file>.meta.json` next to an image records where it came from
(`provider`, `acquired_at`, `license`, `source_ref`). The download script
writes one for every image.

GeoTIFFs keep their projection. PNGs have no georeferencing, so their outlines
are stored in pixel coordinates.

## Using the map UI
Open http://localhost:8081 and press **?** for the full guide. In short:
pick an image, look around with **Pan / select** (click any outline to open
it), mark one object with **Box** (drag a rectangle around it; optionally type what
to look for, e.g. "pepper field") or **Click**,
adjust the outline, type or pick a label, and **Save**. On the **Review** tab,
tick the label under "Complete for this image" once every one of them is
outlined.

Adjusting: drag the white corners, click an edge to add a corner, select a
corner and press Delete. Shift+click a second corner and press **Straighten**
(S) to replace everything between them with a straight line, which also cuts
off a wrong lobe at its neck. Click inside a hole and press Delete to fill it.
**Erase** / **Add** cut out or merge in a shape you draw; **Simplify** drops
nearly-in-line corners. **Discard** / **Reject** keep a wrong outline as a
training example; **Delete** removes it for good.

The **Table** tab lists every saved outline on the image. The Table,
Suggestions and Sweep tabs accept, reject or delete many outlines at once.
"Start over on this image" (left) deletes everything outlined on the image.
The theme button (top) switches between light and dark.

## Status

Works now:
- SAM-3 runs on the local GPU: click and box prompts, text search ("find all
  fields"), "find similar" from an example outline, and tiled sweeps over a
  whole GeoTIFF.
- The map UI, including editing, labels, review and bulk actions.
- Every model proposal and every correction is saved to PostGIS.

Not done yet:
- No adapter has been trained. There are not yet enough accepted outlines.
- Fine-tuning has not been run on a GPU, locally or on the SLURM cluster.
  The code is unit-tested, and `export` runs in the `finetune` image.
- Importing existing labels from Collect Earth Online.

## Using the API from another service

The map UI is one client of the API. Any other service can call it the same
way at `http://localhost:8000`. The full request and response reference is at
http://localhost:8000/docs.

1. Wait for `GET /healthz` to return `{"status":"ready"}`.
2. Find an image: `GET /images`, or register one with `POST /images`
   (`{"source_uri": "/data/images/scene.tif"}`, a path inside the api
   container) or `POST /images/upload`.
3. Ask the model for outlines:
   - `POST /segment/interactive`: points and/or a box on one object.
   - `POST /segment/concept`: every object matching a text prompt and/or
     example outlines.
   - `POST /segment/tiled`: a text prompt over the whole image, run as a
     background job. Poll `GET /segment/jobs/{job_id}`.
4. Review the results through `/annotations`: `GET` to list, `PUT /annotations/{id}`
   to change the outline, label or status (`accepted`, `rejected`),
   `DELETE` to remove, `POST /annotations/bulk` for many at once.
5. Export: `GET /annotations/geojson?image_id=…` returns one image's outlines
   as GeoJSON in WGS84 lon/lat (or in the image's own CRS with `crs=image`).

```bash
curl -X POST localhost:8000/segment/interactive -H 'content-type: application/json' \
  -d '{"image_id": "<image_id>", "points": [{"x": 400, "y": 500, "label": 1}]}'
```

Things to know:
- Every model outline is saved as an annotation with status `proposed` before
  anyone reviews it. Rejected outlines are kept as negative examples;
  `DELETE` removes one for good.
- Point and box coordinates are image pixels by default. Send
  `"coord_space": "image"` to use the image's own CRS instead.
- Returned polygons are in the image's CRS for GeoTIFFs and in pixels for PNGs;
  `properties.coord_space` on each feature says which.

## Fine-tuning

### How it works
Fine-tuning trains a small LoRA adapter on top of the frozen SAM-3 model,
using the outlines people have accepted. The base model is never changed.
`export` splits the accepted outlines into train, validation and test sets by
image, so one scene never appears in two sets, and saves the split in the
database so the test set stays the same between runs. An adapter is used only
if `evaluate` shows it beats the base model on that test set.

### Current state
The pipeline code is written and unit-tested, but it has not been run on a GPU
and no adapter exists. `export` runs in the `finetune` Docker image.

### Stages

| Command | What it does | Needs |
|---|---|---|
| `export` | Accepted outlines to COCO train/valid/test folders | Database |
| `train` | Starts a LoRA training run | GPU, `HF_TOKEN` |
| `status` | Shows a run's state and latest loss | |
| `extract` | Pulls the adapter out of a training checkpoint | |
| `evaluate` | Compares base model and adapter on the test set | GPU, `HF_TOKEN` |
| `publish` | Pushes the adapter to the Hugging Face Hub | Network, `HF_TOKEN`, `HF_ADAPTER_REPO` |
| `slurm-check` | Checks the SLURM cluster setup without submitting | VPN |

### Train on your own GPU
1. In `.env`, set `HF_ADAPTER_REPO` to the Hugging Face model repo that
   adapters are pushed to (e.g. `your-org/sam3-rs-lora`).
2. Make sure `storage` is running, then run the stages:
```bash
set -a; . ./.env; set +a           # puts DATABASE_URL in this shell
alias ft='docker compose --profile finetune run --rm finetune'
ft export --dsn "$DATABASE_URL" --out /data/datasets
ft train  --dataset-dir /data/datasets/remote_sensing \
          --vendor-sam3 /srv/vendor/sam3 --runs-dir /data/runs \
          --dsn "$DATABASE_URL"
ft status --runs-dir /data/runs --run-id <run_id>
ft extract  --checkpoint /data/runs/<run_id>/experiment/checkpoints/checkpoint.pt \
            --out /data/runs/<run_id>/adapter
ft evaluate --dataset-dir /data/datasets/remote_sensing \
            --adapter-dir /data/runs/<run_id>/adapter
ft publish  --adapter-dir /data/runs/<run_id>/adapter --run-id <run_id> \
            --metrics /data/runs/<run_id>/adapter/metrics.json \
            --dsn "$DATABASE_URL"
```
3. If `evaluate` reports `adapter_improves_mean_iou: true`, serve the adapter:
   set `model.adapter_ref: "<repo>@run-<run_id>"` in `config/settings.yaml`
   and restart the api (`docker compose restart api`). Do not serve an
   adapter whose `evaluate` output says `adapter_improves_mean_iou: false`.

The first `ft` command builds the `finetune` image, which is several GB.

### Train on the SLURM cluster
`train --backend slurm` copies the exported dataset to the cluster over
ssh/rsync and submits the job there. `status --backend slurm` reads the job
state and copies logs (and, when the job finishes, checkpoints) back into
`data/runs/<run_id>/experiment/`. `extract`, `evaluate` and `publish` then
work as for a local run.

Requirements:
- VPN connected. The cluster is only reachable over VPN.
- Key-based ssh with a host alias in `~/.ssh/config` (user, key, any
  `ProxyJump`).
- Run these commands on your machine, not in the `finetune` container, so
  they use your VPN and ssh keys.

One-time setup on a cluster login node, in a folder on the shared filesystem:
```bash
git clone --recursive https://github.com/sig-gis/segmentation-assistant.git segsvc
cd segsvc
python -m venv .venv
.venv/bin/pip install -e "vendor/sam3[train]" -e services/finetune
.venv/bin/huggingface-cli login                      # gated facebook/sam3 weights
.venv/bin/huggingface-cli download facebook/sam3     # in case compute nodes have no internet
```

In your local `.env`:
```bash
SLURM_SSH_HOST=<ssh alias>
SLURM_REMOTE_ROOT=/shared/path/segsvc
SLURM_REMOTE_PYTHON=/shared/path/segsvc/.venv/bin/python
SLURM_PARTITION=<partition>
SLURM_ACCOUNT=<account, if required>
SLURM_QOS=<qos, if required>
```

Check the setup, then run:
```bash
set -a; . ./.env; set +a
DSN=postgresql://segsvc:<password>@localhost:5432/segmentation
uv run seg-finetune slurm-check
uv run seg-finetune export --dsn "$DSN" --out data/datasets
uv run seg-finetune train --backend slurm --dataset-dir data/datasets/remote_sensing \
    --vendor-sam3 vendor/sam3 --runs-dir data/runs --num-gpus 1 --dsn "$DSN"
uv run seg-finetune status --backend slurm --runs-dir data/runs --run-id <run_id>
```
`slurm-check` checks ssh and VPN, the remote checkout, the remote Python,
`sbatch` and `sacct`, and the Hugging Face token. Each run lives at
`<SLURM_REMOTE_ROOT>/runs/<run_id>/`. The time limit and CPUs come from the
`submitit:` block in `services/finetune/finetune/configs/sam3_rs_lora.yaml`.

## Running the tests

No GPU, network, model weights or database needed:
```bash
uv run pytest                              # api and finetune
cd services/web && npm ci && npm test      # map UI
```

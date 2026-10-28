# Build context is the repo root (see docker-compose.yml) so the image can
# also install services/finetune — the api imports finetune.lora only when a
# fine-tuned adapter_ref is configured in config/settings.yaml.
#
# First build pulls the full ML
# stack (torch, vendored sam3, segment-geospatial) — expect a large image and a long
# first build. GPU access comes from the NVIDIA container toolkit via the
# `deploy.resources` block in docker-compose.yml.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libexpat1 libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# The heavy ML stack first, in its own layer, so code changes rebuild in
# seconds instead of reinstalling torch. setuptools<81 keeps pkg_resources,
# which sam3 still imports.
RUN pip install --no-cache-dir "segment-geospatial[samgeo3]>=0.12" "setuptools<81"

COPY services/api/ ./api/
COPY services/storage/migrations/ ./migrations/
ENV MIGRATIONS_DIR=/srv/migrations
COPY services/finetune/pyproject.toml ./finetune/pyproject.toml
COPY services/finetune/finetune/ ./finetune/finetune/
COPY vendor/sam3/ ./vendor/sam3/

# The finetune package is installed without its [train] extra: only the LoRA
# injection utilities are needed at inference time.
RUN pip install --no-cache-dir ./api[ml] ./finetune "setuptools<81"
# sam3 then comes from the pinned vendored checkout, without touching other
# packages: the PyPI `sam3` that [ml] resolves to is an empty placeholder, and
# the vendored one's numpy<2 pin conflicts with the rest of the stack.
RUN pip install --no-cache-dir --no-deps --force-reinstall ./vendor/sam3

# Weights are NOT baked into the image: they are gated behind the facebook/sam3
# license and download on first startup using HF_TOKEN from the environment.

EXPOSE 8000
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]

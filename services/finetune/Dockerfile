# Build context is the repo root (see docker-compose.yml) so the vendored
# sam3 checkout is available. Not yet built; the [train]
# stack is multi-GB and training is only ever triggered explicitly via
#   docker compose --profile finetune run finetune <subcommand>
#
# Weights are not baked in: the gated facebook/sam3 checkpoint downloads at
# train time using HF_TOKEN.

FROM pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime

WORKDIR /srv

COPY vendor/sam3 ./vendor/sam3
COPY services/finetune ./finetune

# vendored checkout first (its [train] extra brings hydra/submitit/tensorboard
# and makes the training configs available on disk), then our package.
RUN pip install --no-cache-dir -e "./vendor/sam3[train]" \
    && pip install --no-cache-dir -e "./finetune[db,train]"

ENTRYPOINT ["seg-finetune"]
CMD ["--help"]

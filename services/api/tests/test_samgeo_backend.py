"""Regression tests for SamGeoBackend's tensor handling.

These exist because of a real production 500: every `/segment/concept` request
carrying an exemplar crashed with

    TypeError: Got unsupported ScalarType BFloat16

The concept path runs inside `torch.autocast(dtype=bfloat16)`, and NumPy has no
bfloat16, so `.numpy()` on the returned scores raises. The mocked suite could
not catch it because nothing here imports torch — so `FakeTensor` below
reproduces torch's exact behaviour (including raising on `.numpy()` for half
precision dtypes) and lets the bug be caught without a GPU.
"""

from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import pytest

from app.backends.base import ExemplarBox
from app.backends.samgeo_backend import SamGeoBackend, to_numpy
from app.config import Settings
from app.repository import ImageRecord


# how torch spells these dtypes in the TypeError it raises
_TORCH_SCALAR_NAMES = {"torch.bfloat16": "BFloat16", "torch.float16": "Half"}


class FakeTensor:
    """Minimal stand-in for torch.Tensor that mirrors the dtype restriction."""

    def __init__(self, array, dtype: str = "torch.float32"):
        self._array = np.asarray(array)
        self.dtype = dtype
        self.detach_calls = 0

    def detach(self):
        self.detach_calls += 1
        return self

    def cpu(self):
        return self

    def float(self):
        return FakeTensor(self._array.astype(np.float32), "torch.float32")

    def numpy(self):
        if self.dtype in _TORCH_SCALAR_NAMES:
            # verbatim torch wording, so the guard test is meaningful
            raise TypeError(
                f"Got unsupported ScalarType {_TORCH_SCALAR_NAMES[self.dtype]}"
            )
        return self._array


# ---------------------------------------------------------------------------
# to_numpy
# ---------------------------------------------------------------------------

def test_bfloat16_is_upcast_instead_of_raising():
    tensor = FakeTensor([0.5, 0.25], dtype="torch.bfloat16")
    result = to_numpy(tensor)
    assert isinstance(result, np.ndarray)
    assert result.tolist() == [0.5, 0.25]


def test_float16_is_also_upcast():
    assert to_numpy(FakeTensor([1.0], dtype="torch.float16")).tolist() == [1.0]


def test_float32_passes_through_without_conversion():
    tensor = FakeTensor([1.0, 2.0], dtype="torch.float32")
    assert to_numpy(tensor).tolist() == [1.0, 2.0]


def test_bool_masks_stay_bool():
    tensor = FakeTensor(np.array([True, False]), dtype="torch.bool")
    result = to_numpy(tensor)
    assert result.dtype == np.bool_


def test_plain_numpy_input_is_returned_unchanged():
    array = np.array([1, 2, 3])
    assert to_numpy(array) is array


def test_the_raw_call_really_would_have_raised():
    """Guards the guard: if FakeTensor stopped mimicking torch, the tests above
    would pass vacuously."""
    with pytest.raises(TypeError, match="BFloat16"):
        FakeTensor([1.0], dtype="torch.bfloat16").numpy()


# ---------------------------------------------------------------------------
# _concept_with_exemplars
# ---------------------------------------------------------------------------

class FakeProcessor:
    def __init__(self, state):
        self._state = state
        self.reset_calls = 0
        self.text_prompts: list[str] = []
        self.boxes: list[tuple[list[float], bool]] = []

    def reset_all_prompts(self, state):
        self.reset_calls += 1

    def set_text_prompt(self, prompt, state):
        self.text_prompts.append(prompt)
        return self._state

    def add_geometric_prompt(self, box, label, state):
        self.boxes.append((box, label))
        return self._state


class FakeSam:
    """Stands in for SamGeo3 — scores come back bfloat16, as on a real GPU."""

    backend = "meta"

    def __init__(self, num_instances: int = 2, width: int = 100, height: int = 200):
        masks = np.zeros((num_instances, 1, height, width), dtype=bool)
        for i in range(num_instances):
            masks[i, 0, 10 : 10 + 5 * (i + 1), 10 : 10 + 5 * (i + 1)] = True
        state = {
            "original_width": width,
            "original_height": height,
            "masks": FakeTensor(masks, dtype="torch.bool"),
            "scores": FakeTensor(
                np.array([0.9, 0.8][:num_instances]), dtype="torch.bfloat16"
            ),
        }
        self.inference_state = state
        self.processor = FakeProcessor(state)

    @contextmanager
    def _meta_autocast(self):
        yield


@pytest.fixture
def backend_and_image(tmp_path):
    settings = Settings(database_url="", data_dir=tmp_path)
    backend = SamGeoBackend(settings)
    image = ImageRecord(
        image_id="00000000-0000-0000-0000-000000000001",
        source_uri=str(tmp_path / "img.png"),
        kind="png",
        crs=None,
        width=100,
        height=200,
        checksum=None,
    )
    return backend, image


def test_exemplar_path_survives_bfloat16_scores(backend_and_image):
    """The exact production failure: this raised TypeError before the fix."""
    backend, image = backend_and_image
    sam = FakeSam(num_instances=2)

    masks, scores = backend._concept_with_exemplars(
        sam, image, "field", [ExemplarBox(box=(10, 20, 30, 40), positive=True)]
    )

    assert masks.shape == (2, 200, 100)  # leading N, channel axis squeezed
    assert np.asarray(scores).tolist() == pytest.approx([0.9, 0.8])


def test_box_is_normalized_to_centre_width_height(backend_and_image):
    """add_geometric_prompt wants [cx, cy, w, h] normalized to 0-1, not XYXY."""
    backend, image = backend_and_image
    sam = FakeSam()

    backend._concept_with_exemplars(
        sam, image, None, [ExemplarBox(box=(10, 20, 30, 60), positive=True)]
    )

    (box, label), = sam.processor.boxes
    # width 100, height 200 → centre (20, 40) → (0.2, 0.2); size (20, 40) → (0.2, 0.2)
    assert box == pytest.approx([0.2, 0.2, 0.2, 0.2])
    assert label is True
    assert sam.processor.reset_calls == 1
    assert sam.processor.text_prompts == []  # no text given


def test_negative_exemplar_passes_label_false(backend_and_image):
    backend, image = backend_and_image
    sam = FakeSam()

    backend._concept_with_exemplars(
        sam,
        image,
        "field",
        [
            ExemplarBox(box=(0, 0, 10, 10), positive=True),
            ExemplarBox(box=(50, 50, 60, 60), positive=False),
        ],
    )

    assert [label for _, label in sam.processor.boxes] == [True, False]
    assert sam.processor.text_prompts == ["field"]


def test_transformers_backend_is_refused_clearly(backend_and_image):
    backend, image = backend_and_image
    sam = FakeSam()
    sam.backend = "transformers"

    with pytest.raises(NotImplementedError, match="meta"):
        backend._concept_with_exemplars(
            sam, image, "field", [ExemplarBox(box=(0, 0, 1, 1))]
        )


class FakeSweepSam:
    """SamGeo3 stand-in for sweeps: finds one 10x10 square in every tile."""

    backend = "meta"

    def __init__(self):
        self.images = []

    def set_image(self, image):
        self.images.append(image)

    def generate_masks(self, prompt, quiet=False):
        h, w = self.images[-1].shape[:2]
        mask = np.zeros((h, w), dtype=bool)
        mask[2:12, 2:12] = True
        # numpy N x 1 x H x W, as samgeo returns after its numpy conversion
        self.masks, self.scores = mask[None, None], np.array([0.7])


def test_sweep_runs_every_tile_and_writes_scored_outlines(tmp_path):
    import json

    from PIL import Image

    path = tmp_path / "img.png"
    Image.fromarray(np.zeros((100, 150, 3), dtype=np.uint8)).save(path)
    settings = Settings(database_url="", data_dir=tmp_path)
    backend = SamGeoBackend(settings)
    backend._sam = FakeSweepSam()
    backend._current_source = "something else"
    image = ImageRecord(image_id="00000000-0000-0000-0000-000000000001",
                        source_uri=str(path), kind="png", crs=None,
                        width=150, height=100, checksum=None)

    result = backend.run_tiled(image, "field", 100, 20, str(tmp_path / "out"))

    assert len(backend._sam.images) == 2           # two 100-px tiles across
    assert backend._current_source is None         # the next click re-sets the image
    with open(result["vector_uri"]) as f:
        features = json.load(f)["features"]
    assert len(features) == 2
    assert all(f["properties"]["score"] == 0.7 for f in features)

"""8-bit RGB for SAM-3 and the map, with one brightness stretch per image.

SAM-3 takes 8-bit RGB. 8-bit images pass through unchanged; anything else
(e.g. 16-bit Sentinel-2) is stretched so the 2nd-98th percentile of each band,
measured over the whole image, maps to 0-255. Using the same limits for every
tile means the same ground looks the same in every tile (samgeo stretches
each tile on its own, which shows as brightness steps at tile edges).
"""

from __future__ import annotations

import warnings

import numpy as np

_SAMPLE_SIZE = 1024


def _bands(src) -> list[int]:
    return [1, 2, 3] if src.count >= 3 else [1, 1, 1]


def stretch_limits(src, bands: list[int]) -> list[tuple[float, float]]:
    """2nd-98th percentile per band, from a reduced read of the image."""
    scale = max(1, max(src.width, src.height) // _SAMPLE_SIZE)
    shape = (len(bands), max(1, src.height // scale), max(1, src.width // scale))
    sample = src.read(bands, out_shape=shape, masked=True)
    limits = []
    for band in sample:
        values = band.compressed()
        if values.size == 0:
            limits.append((0.0, 1.0))
            continue
        lo, hi = np.percentile(values, (2, 98))
        limits.append((float(lo), float(hi) if hi > lo else float(lo) + 1.0))
    return limits


def apply_limits(data, limits) -> np.ndarray:
    """Masked (bands, rows, cols) array -> float 0-255, unclipped."""
    if limits is None:
        return data.filled(0).astype(np.float32)
    return np.stack([
        (band.filled(lo).astype(np.float32) - lo) / (hi - lo) * 255.0
        for band, (lo, hi) in zip(data, limits)
    ])


def _open(source_uri: str):
    import rasterio

    with warnings.catch_warnings():  # PNGs have no georeferencing, by design
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        return rasterio.open(source_uri)


def image_limits(source_uri: str) -> list[tuple[float, float]] | None:
    """Stretch limits for the whole image; None for 8-bit images."""
    with _open(source_uri) as src:
        return None if src.dtypes[0] == "uint8" else stretch_limits(src, _bands(src))


def read_rgb(source_uri: str, box: tuple[int, int, int, int], limits) -> np.ndarray:
    """One pixel box (x0, y0, x1, y1) as rows x cols x 3 uint8."""
    from rasterio.windows import Window

    x0, y0, x1, y1 = box
    with _open(source_uri) as src:
        data = src.read(_bands(src), window=Window(x0, y0, x1 - x0, y1 - y0), masked=True)
    return np.clip(apply_limits(data, limits), 0, 255).astype(np.uint8).transpose(1, 2, 0)

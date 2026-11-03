"""Browser-ready copies of GeoTIFFs.

A web map (OpenLayers) reads GeoTIFFs over HTTP by fetching only the byte
ranges it needs, which is fast only for a Cloud-Optimized GeoTIFF (COG): a
GeoTIFF laid out in tiles with smaller zoomed-out copies ("overviews")
inside. Source images are often not laid out that way and may be 16-bit or
multi-band, so each GeoTIFF gets a display copy: 3 bands, 8-bit, COG, same
projection and pixel grid, with 0 reserved for "no data". The original file
is untouched and remains what the model reads.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .stretch import apply_limits, stretch_limits

_STRIP_ROWS = 1024


def build_display_cog(source_uri: str, out_path: Path) -> Path:
    """Write the display copy of a GeoTIFF to `out_path` and return it."""
    import rasterio
    from rasterio.shutil import copy as rio_copy
    from rasterio.windows import Window

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.stem + ".tmp.tif")

    with rasterio.open(source_uri) as src:
        bands = [1, 2, 3] if src.count >= 3 else [1, 1, 1]
        # 8-bit sources keep their colors; anything else is contrast-stretched
        limits = None if src.dtypes[0] == "uint8" else stretch_limits(src, bands)
        profile = {
            "driver": "GTiff", "width": src.width, "height": src.height, "count": 3,
            "dtype": "uint8", "crs": src.crs, "transform": src.transform, "nodata": 0,
            "tiled": True, "blockxsize": 512, "blockysize": 512, "compress": "deflate",
        }
        with rasterio.open(tmp_path, "w", **profile) as dst:
            for row in range(0, src.height, _STRIP_ROWS):
                window = Window(0, row, src.width, min(_STRIP_ROWS, src.height - row))
                data = src.read(bands, window=window, masked=True)
                values = apply_limits(data, limits)
                # real pixels are 1..255 so 0 can mean "no data"
                out = np.clip(values, 1, 255).astype(np.uint8)
                invalid = np.ma.getmaskarray(data).any(axis=0)
                out[:, invalid] = 0
                dst.write(out, window=window)

    rio_copy(tmp_path, out_path, driver="COG", compress="DEFLATE",
             overview_resampling="AVERAGE")
    tmp_path.unlink()
    return out_path

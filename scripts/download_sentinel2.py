"""Download small Sentinel-2 true-color GeoTIFFs for testing.

Searches the free Earth Search catalog (Sentinel-2 Level-2A on AWS, no account
needed), takes the least cloudy scene that fully covers the area, and saves
just that area as an RGB GeoTIFF (10 m pixels, the scene's UTM projection).
Each image gets a `<name>.meta.json` provenance file (provider, date,
license, scene id) that the API reads when it registers the image.
Files written to data/images/ show up in the app automatically.

Usage:
    .venv/bin/python scripts/download_sentinel2.py --list
    .venv/bin/python scripts/download_sentinel2.py mekong-rice rondonia-lots
    .venv/bin/python scripts/download_sentinel2.py --all
    .venv/bin/python scripts/download_sentinel2.py --bbox -93.65 41.95 -93.55 42.05 \\
        --dates 2026-06-01/2026-09-15 --name my-area
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

STAC_SEARCH = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"

# name: (bbox lon_min, lat_min, lon_max, lat_max), date range, description.
# Areas are about 11 x 11 km (~1100 x 1100 pixels). Dates target dry seasons.
PRESETS = {
    "mekong-rice": (
        (105.40, 10.30, 105.50, 10.40), "2026-01-01/2026-04-30",
        "Mekong Delta, Vietnam: long narrow rice fields along canals",
    ),
    "thailand-rice": (
        (100.05, 14.40, 100.15, 14.50), "2026-01-01/2026-04-30",
        "Central Thailand: small rice fields",
    ),
    "rondonia-lots": (
        (-63.10, -10.00, -63.00, -9.90), "2026-06-01/2026-09-15",
        "Rondonia, Brazil: long farm lots running off roads",
    ),
    "iowa-fields": (
        (-93.65, 41.95, -93.55, 42.05), "2026-06-01/2026-09-15",
        "Iowa, USA: large regular fields (easy control case)",
    ),
}


def search(bbox, dates, max_cloud, limit=20):
    body = {
        "collections": [COLLECTION],
        "bbox": list(bbox),
        "datetime": f"{dates.split('/')[0]}T00:00:00Z/{dates.split('/')[1]}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        "limit": limit,
    }
    request = urllib.request.Request(
        STAC_SEARCH, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)["features"]


def read_area(href, bbox):
    """Read only the bbox window from the remote image (no full-scene download)."""
    with rasterio.open(href) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, *bbox)
        window = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
        data = src.read(window=window, boundless=True, fill_value=0)
        profile = src.profile.copy()
        profile.update(
            driver="GTiff", width=window.width, height=window.height,
            transform=src.window_transform(window), compress="deflate",
            tiled=True, blockxsize=256, blockysize=256, nodata=0,
        )
    return data, profile


def write_provenance(path, date, scene_id):
    """Sidecar read by the API at registration (app/imaging.py)."""
    meta = {
        "provider": "Copernicus Sentinel-2 L2A (Earth Search, AWS)",
        "acquired_at": date,
        "license": "Copernicus Sentinel data, free and open (CC BY-SA 3.0 IGO)",
        "source_ref": scene_id,
    }
    sidecar = path.with_name(path.name + ".meta.json")
    sidecar.write_text(json.dumps(meta, indent=2) + "\n")


def download(name, bbox, dates, out_dir, max_cloud):
    items = search(bbox, dates, max_cloud)
    if not items:
        print(f"[{name}] no scenes under {max_cloud}% cloud in {dates}")
        return None
    for item in items:
        data, profile = read_area(item["assets"]["visual"]["href"], bbox)
        empty = float(np.mean(np.all(data == 0, axis=0)))
        if empty > 0.01:  # area falls partly off this scene's edge
            continue
        date = item["properties"]["datetime"][:10]
        out = out_dir / f"s2_{name}_{date}.tif"
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(data)
        write_provenance(out, date, item["id"])
        cloud = item["properties"]["eo:cloud_cover"]
        print(f"[{name}] {out} ({data.shape[2]}x{data.shape[1]} px, "
              f"scene cloud {cloud:.1f}%, {item['id']})")
        return out
    print(f"[{name}] no scene fully covers the area; try a wider date range")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("presets", nargs="*", help="preset names (see --list)")
    parser.add_argument("--all", action="store_true", help="download every preset")
    parser.add_argument("--list", action="store_true", help="show presets and exit")
    parser.add_argument("--bbox", nargs=4, type=float,
                        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    parser.add_argument("--dates", help="YYYY-MM-DD/YYYY-MM-DD (with --bbox)")
    parser.add_argument("--name", help="file name part (with --bbox)")
    parser.add_argument("--max-cloud", type=float, default=10.0,
                        help="max scene cloud cover in percent (default 10)")
    parser.add_argument("--out", default="data/images", help="output folder")
    args = parser.parse_args(argv)

    if args.list:
        for name, (bbox, dates, text) in PRESETS.items():
            print(f"{name:15} {dates}  {text}")
        return 0

    jobs = []
    if args.bbox:
        if not (args.dates and args.name):
            parser.error("--bbox needs --dates and --name")
        jobs.append((args.name, tuple(args.bbox), args.dates))
    names = list(PRESETS) if args.all else args.presets
    for name in names:
        if name not in PRESETS:
            parser.error(f"unknown preset {name!r}; see --list")
        bbox, dates, _ = PRESETS[name]
        jobs.append((name, bbox, dates))
    if not jobs:
        parser.error("give preset names, --all, or --bbox")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = [name for name, bbox, dates in jobs
              if download(name, bbox, dates, out_dir, args.max_cloud) is None]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

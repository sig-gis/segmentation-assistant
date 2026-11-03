"""Join objects that a tiled run cut at tile edges.

Neighbouring tiles overlap, so an object crossing a tile edge is seen by both
tiles inside the shared strip ("band"). Two pieces are joined only when they
come from different tiles and their parts inside the band match well (IoU at
least `min_iou`). Pieces that merely touch are not joined: two neighbouring
fields with the same label must stay two outlines, because a false merge
does more harm to training data than a cut.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely import make_valid
from shapely.geometry import box as make_box
from shapely.ops import unary_union
from shapely.strtree import STRtree


@dataclass
class Piece:
    tile: int
    geometry: object  # shapely geometry, pixel coordinates
    score: float | None


@dataclass
class Joined:
    geometry: object
    score: float | None
    pieces: list = field(default_factory=list)


def _band(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return make_box(x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _same_object(a: Piece, b: Piece, tiles, min_iou: float) -> bool:
    band = _band(tiles[a.tile], tiles[b.tile])
    if band is None:
        return False
    pa, pb = a.geometry.intersection(band), b.geometry.intersection(band)
    if pa.is_empty or pb.is_empty:
        return False
    union = pa.union(pb).area
    return union > 0 and pa.intersection(pb).area / union >= min_iou


def join_across_seams(pieces: list[Piece], tiles, min_iou: float = 0.5,
                      min_size: float = 0, max_size: float | None = None) -> list[Joined]:
    """Group pieces into objects and drop objects outside min/max size (px²).
    Invalid pieces are repaired first, so one bad mask cannot fail a sweep."""
    pieces = [p if p.geometry.is_valid else Piece(p.tile, make_valid(p.geometry), p.score)
              for p in pieces]
    parent = list(range(len(pieces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    tree = STRtree([p.geometry for p in pieces])
    for i, piece in enumerate(pieces):
        for j in tree.query(piece.geometry):
            j = int(j)
            if j > i and pieces[j].tile != piece.tile and \
                    _same_object(piece, pieces[j], tiles, min_iou):
                parent[find(j)] = find(i)

    groups: dict[int, list[Piece]] = {}
    for i, piece in enumerate(pieces):
        groups.setdefault(find(i), []).append(piece)

    joined = []
    for group in groups.values():
        geometry = unary_union([p.geometry for p in group])
        if geometry.area < min_size or (max_size is not None and geometry.area > max_size):
            continue
        scores = [p.score for p in group if p.score is not None]
        joined.append(Joined(geometry=geometry, score=max(scores) if scores else None,
                             pieces=[p.geometry for p in group]))
    return joined

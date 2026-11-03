from shapely.geometry import box

from app.seams import Piece, join_across_seams

# two tiles side by side, 100 px wide, overlapping in x 80..100
TILES = [(0, 0, 100, 100), (80, 0, 180, 100)]


def test_object_crossing_the_seam_is_joined():
    # one field 50..150: tile 0 sees 50..100, tile 1 sees 80..150
    pieces = [Piece(0, box(50, 10, 100, 60), 0.7), Piece(1, box(80, 10, 150, 60), 0.9)]
    joined = join_across_seams(pieces, TILES)
    assert len(joined) == 1
    assert joined[0].geometry.equals(box(50, 10, 150, 60))
    assert joined[0].score == 0.9
    assert len(joined[0].pieces) == 2


def test_two_neighbouring_fields_crossing_the_seam_stay_apart():
    # field A 30..90 and field B 90..160 meet inside the overlap band
    pieces = [
        Piece(0, box(30, 10, 90, 60), 0.8), Piece(0, box(90, 10, 100, 60), 0.8),
        Piece(1, box(80, 10, 90, 60), 0.8), Piece(1, box(90, 10, 160, 60), 0.8),
    ]
    joined = join_across_seams(pieces, TILES)
    shapes = sorted(j.geometry.bounds for j in joined)
    assert shapes == [(30, 10, 90, 60), (90, 10, 160, 60)]


def test_pieces_that_only_touch_are_not_joined():
    pieces = [Piece(0, box(40, 10, 85, 60), 0.8), Piece(1, box(85, 10, 150, 60), 0.8)]
    assert len(join_across_seams(pieces, TILES)) == 2


def test_pieces_from_the_same_tile_are_never_joined():
    pieces = [Piece(0, box(10, 10, 50, 50), 0.8), Piece(0, box(30, 30, 70, 70), 0.8)]
    assert len(join_across_seams(pieces, TILES)) == 2


def test_an_object_seen_whole_by_both_tiles_is_kept_once():
    pieces = [Piece(0, box(82, 10, 98, 30), 0.6), Piece(1, box(82, 10, 98, 30), 0.8)]
    joined = join_across_seams(pieces, TILES)
    assert len(joined) == 1 and joined[0].score == 0.8


def test_size_limits_apply_to_joined_objects():
    pieces = [Piece(0, box(50, 10, 100, 60), 0.7), Piece(1, box(80, 10, 150, 60), 0.9),
              Piece(0, box(5, 70, 10, 75), 0.9)]  # 25 px²
    joined = join_across_seams(pieces, TILES, min_size=100, max_size=6000)
    assert [j.geometry.area for j in joined] == [5000]
    assert join_across_seams(pieces, TILES, max_size=4000)[0].geometry.area == 25

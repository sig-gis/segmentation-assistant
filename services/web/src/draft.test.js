import { describe, expect, it } from "vitest";
import {
  boxToApi, cornerCount, cornersBetween, deleteVertex, edit, extentOf, holeAt, holeCount,
  nearestVertex, newDraft, removeHole, simplify, simplifyTo, straighten, undo,
  vertexAt, vertices,
} from "./draft.js";

const square = {
  type: "MultiPolygon",
  coordinates: [[[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]],
};
const withHole = {
  type: "MultiPolygon",
  coordinates: [[
    [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
    [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
  ]],
};

describe("draft history", () => {
  it("edit then undo restores the previous shape", () => {
    const d = newDraft({ annotationId: "a", geometry: square, kind: "model", label: "field" });
    const moved = edit(d, withHole);
    expect(moved.dirty).toBe(true);
    const back = undo(moved);
    expect(back.geometry).toBe(square);
    expect(back.dirty).toBe(false);
    expect(undo(back)).toBe(back); // nothing left to undo
  });
});

describe("corners", () => {
  it("lists corners without closing duplicates", () => {
    expect(vertices(square)).toHaveLength(4);
    expect(cornerCount(withHole)).toBe(8);
    expect(holeCount(withHole)).toBe(1);
  });

  it("finds the nearest corner within tolerance", () => {
    expect(nearestVertex(square, [9.5, 0.4], 1)).toEqual([0, 0, 1]);
    expect(nearestVertex(square, [5, 5], 1)).toBe(null);
    expect(vertexAt(square, [0, 0, 2])).toEqual([10, 10]);
  });

  it("deletes a corner and keeps the ring closed", () => {
    const pentagon = {
      type: "MultiPolygon",
      coordinates: [[[[0, 0], [5, -2], [10, 0], [10, 10], [0, 10], [0, 0]]]],
    };
    const removed = deleteVertex(pentagon, [0, 0, 0]);
    const ring = removed.coordinates[0][0];
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[ring.length - 1]);
  });

  it("refuses to go below three corners", () => {
    const triangle = {
      type: "MultiPolygon",
      coordinates: [[[[0, 0], [10, 0], [0, 10], [0, 0]]]],
    };
    expect(deleteVertex(triangle, [0, 0, 1])).toBe(null);
  });
});

describe("boxToApi", () => {
  it("normalizes corners after converting (y may flip)", () => {
    const flip = ([x, y]) => [x, 100 - y];
    expect(boxToApi([10, 20, 30, 40], flip)).toEqual([10, 60, 30, 80]);
  });
});

describe("extentOf", () => {
  it("is the bounding box", () => {
    expect(extentOf(withHole)).toEqual([0, 0, 10, 10]);
  });
});

// a 10x10 square with corners along the bottom edge, nearly in line
const wobbly = {
  type: "MultiPolygon",
  coordinates: [[[
    [0, 0], [2, 0.3], [4, -0.2], [6, 0.2], [8, -0.3], [10, 0], [10, 10], [0, 10], [0, 0],
  ]]],
};

describe("straighten", () => {
  it("removes the corners between two selected corners the short way round", () => {
    const result = straighten(wobbly, [0, 0, 0], [0, 0, 5]);
    expect(result.coordinates[0][0]).toEqual([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]);
  });

  it("flip removes the other side instead", () => {
    expect(cornersBetween(wobbly, [0, 0, 0], [0, 0, 5], true)).toEqual([6, 7]);
  });

  it("needs two corners on the same ring", () => {
    expect(straighten(withHole, [0, 0, 0], [0, 1, 0])).toBe(null);
    expect(straighten(wobbly, [0, 0, 2], [0, 0, 2])).toBe(null);
  });

  it("cuts off a lobe joined by a neck", () => {
    // mushroom: square base with a lobe sticking up between corners 3 and 7
    const mushroom = {
      type: "MultiPolygon",
      coordinates: [[[
        [0, 0], [10, 0], [10, 10], [6, 10], [8, 14], [5, 16], [2, 14], [4, 10], [0, 10], [0, 0],
      ]]],
    };
    const result = straighten(mushroom, [0, 0, 3], [0, 0, 7]);
    expect(result.coordinates[0][0]).toEqual(
      [[0, 0], [10, 0], [10, 10], [6, 10], [4, 10], [0, 10], [0, 0]]
    );
  });
});

describe("simplify", () => {
  it("drops corners within the tolerance of a straight line", () => {
    expect(cornerCount(simplify(wobbly, 0.5))).toBe(4);
    expect(cornerCount(simplify(wobbly, 0.1))).toBe(cornerCount(wobbly));
  });
});

describe("holes", () => {
  it("finds the hole under a point and removes it", () => {
    expect(holeAt(withHole, [5, 5])).toEqual([0, 1]);
    expect(holeAt(withHole, [1, 1])).toBe(null);
    expect(holeCount(removeHole(withHole, [0, 1]))).toBe(0);
  });
});

describe("simplifyTo", () => {
  it("is reversible: 4 px then 1 px then off", () => {
    const d = newDraft({ annotationId: "a", geometry: wobbly, kind: "model", label: "x" });
    const coarse = simplifyTo(d, 4, 4);
    expect(cornerCount(coarse.geometry)).toBe(4);
    const fine = simplifyTo(coarse, 1, 0.1);
    expect(cornerCount(fine.geometry)).toBe(cornerCount(wobbly));
    expect(simplifyTo(fine, 0, 0).geometry).toBe(wobbly);
    expect(fine.simplifyLevel).toBe(1);
  });

  it("a later edit becomes the new starting point", () => {
    const d = simplifyTo(newDraft({ annotationId: "a", geometry: wobbly, kind: "model", label: "x" }), 4, 4);
    const edited = edit(d, withHole);
    expect(edited.simplifyBase).toBe(null);
    expect(simplifyTo(edited, 0, 0).geometry).toBe(withHole);
  });
});

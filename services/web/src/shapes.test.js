import { describe, expect, it } from "vitest";
import { combine } from "./shapes.js";

const square = (x0, y0, size) => ({
  type: "Polygon",
  coordinates: [[[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]],
});
const area = (multi) =>
  multi.coordinates.reduce((sum, polygon) => {
    const ring = polygon[0];
    let a = 0;
    for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
    return sum + Math.abs(a) / 2;
  }, 0);

describe("combine", () => {
  it("erase cuts the drawn shape out", () => {
    expect(area(combine(square(0, 0, 10), square(5, 0, 10), "erase"))).toBe(50);
  });
  it("add merges the drawn shape in", () => {
    expect(area(combine(square(0, 0, 10), square(5, 0, 10), "add"))).toBe(150);
  });
  it("erasing everything gives null", () => {
    expect(combine(square(0, 0, 10), square(-5, -5, 30), "erase")).toBe(null);
  });
});

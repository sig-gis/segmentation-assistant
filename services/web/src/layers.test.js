import { describe, expect, it } from "vitest";
import { defaultVisibility, labelColor, toggleLabel, visibleFeatures } from "./layers.js";

const f = (id, status, label = "field") => ({ id, properties: { status, label } });
const fc = { features: [f("a", "accepted"), f("b", "proposed"), f("c", "rejected"),
                        f("d", "accepted", "water"), f("draft", "proposed")] };

describe("visibleFeatures", () => {
  it("shows saved outlines only by default, never rejected or the draft", () => {
    const v = visibleFeatures(fc, defaultVisibility, "draft");
    expect(v.saved.map((x) => x.id)).toEqual(["a", "d"]);
    expect(v.suggestions).toEqual([]);
  });

  it("shows suggestions when turned on", () => {
    const v = visibleFeatures(fc, { ...defaultVisibility, suggestions: true }, "draft");
    expect(v.suggestions.map((x) => x.id)).toEqual(["b"]);
  });

  it("hides labels", () => {
    const v = visibleFeatures(fc, toggleLabel(defaultVisibility, "water"), null);
    expect(v.saved.map((x) => x.id)).toEqual(["a"]);
    expect(toggleLabel(toggleLabel(defaultVisibility, "water"), "water").hiddenLabels).toEqual([]);
  });

  it("hides everything saved when the layer is off", () => {
    expect(visibleFeatures(fc, { ...defaultVisibility, saved: false }, null).saved).toEqual([]);
  });
});

describe("labelColor", () => {
  it("is stable per label", () => {
    expect(labelColor("field")).toBe(labelColor("field"));
    expect(labelColor("field")).toMatch(/^#/);
  });
});

describe("skipping", () => {
  it("hides several ids (the draft and the outline it replaces)", () => {
    const v = visibleFeatures(fc, defaultVisibility, ["a", "draft"]);
    expect(v.saved.map((x) => x.id)).toEqual(["d"]);
  });
});

import { describe, expect, it } from "vitest";
import { nextId, origin, reviewQueue, sweepCards, tableSections } from "./review.js";

const feature = (id, props) => ({ id, properties: { status: "proposed", ...props } });
const fc = {
  features: [
    feature("click", { prompt_type: "point", score: 0.5 }),
    feature("similar", { prompt_type: "exemplar", score: 0.9 }),
    feature("sweep", { prompt_type: "text", job_id: "j1", score: null }),
    feature("done", { prompt_type: "point", score: 0.99, status: "accepted" }),
  ],
};

describe("origin", () => {
  it("classifies clicks, find similar and sweeps", () => {
    expect(fc.features.map((f) => origin(f.properties))).toEqual([
      "click", "similar", "sweep", "click",
    ]);
  });
});

describe("reviewQueue", () => {
  it("holds proposed only, most confident first, unscored last", () => {
    expect(reviewQueue(fc)).toEqual(["similar", "click", "sweep"]);
  });
  it("filters by origin", () => {
    expect(reviewQueue(fc, { origin: "sweep" })).toEqual(["sweep"]);
  });
  it("score filter keeps unscored sweep proposals", () => {
    expect(reviewQueue(fc, { minScore: 0.8 })).toEqual(["similar", "sweep"]);
  });
  it("handles no data", () => {
    expect(reviewQueue(null)).toEqual([]);
  });
});

describe("nextId", () => {
  const q = ["a", "b", "c"];
  it("moves forward and wraps", () => {
    expect(nextId(q, "a")).toBe("b");
    expect(nextId(q, "c")).toBe("a");
    expect(nextId(q, "a", -1)).toBe("c");
  });
  it("starts at the top when the current item left the queue", () => {
    expect(nextId(q, "gone")).toBe("a");
    expect(nextId([], "a")).toBe(null);
  });
});

describe("sweeps", () => {
  it("filters the queue to one sweep", () => {
    expect(reviewQueue(fc, { jobId: "j1" })).toEqual(["sweep"]);
  });
  it("lists every sweep, newest first as the API gives them, with outline counts", () => {
    const jobs = [
      { job_id: "j2", status: "running", params: { text: "water" } },
      { job_id: "j1", status: "succeeded", params: { text: "field" } },
    ];
    expect(sweepCards(jobs, fc)).toEqual([
      { jobId: "j2", label: "water", job: jobs[0], proposed: 0, accepted: 0, rejected: 0,
        ids: { proposed: [], accepted: [], rejected: [] } },
      { jobId: "j1", label: "field", job: jobs[1], proposed: 1, accepted: 0, rejected: 0,
        ids: { proposed: ["sweep"], accepted: [], rejected: [] } },
    ]);
  });
  it("keeps a sweep whose outlines were all deleted", () => {
    const jobs = [{ job_id: "gone", status: "succeeded", params: { text: "field" } }];
    expect(sweepCards(jobs, fc)[0].proposed).toBe(0);
  });
});

describe("tableSections", () => {
  const rows = {
    features: [
      feature("a", { job_id: "old" }),
      feature("b", { job_id: "new" }),
      feature("c", { prompt_type: "point" }),
      feature("d", { job_id: "new", status: "accepted" }),
      feature("e", { status: "rejected" }),
      feature("f", { job_id: "unlisted" }),
    ],
  };
  const sweeps = [{ jobId: "new", label: "water" }, { jobId: "empty", label: "x" },
                  { jobId: "old", label: "field" }];

  it("puts accepted and rejected outlines under Reviewed", () => {
    expect(tableSections(rows, sweeps).reviewed.map((f) => f.id)).toEqual(["d", "e"]);
  });
  it("groups proposed outlines by sweep in sweep order, then the rest", () => {
    const groups = tableSections(rows, sweeps).proposed;
    expect(groups.map((g) => [g.jobId, g.label, g.features.map((f) => f.id)])).toEqual([
      ["new", "water", ["b"]],
      ["old", "field", ["a"]],
      [null, null, ["c", "f"]],
    ]);
  });
  it("has no groups when nothing is proposed", () => {
    expect(tableSections({ features: [] }, sweeps)).toEqual({ reviewed: [], proposed: [] });
  });
});

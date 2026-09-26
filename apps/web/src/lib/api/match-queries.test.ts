import { describe, expect, it } from "vitest";

import { matchDetail, requirementResult } from "@/test/match-fixtures";

import {
  CATEGORY_ORDER,
  asPercentage,
  axisCounts,
  groupByCategory,
} from "./match-queries";

/**
 * The reading helpers, tested without rendering.
 *
 * None of these decide anything — they arrange verdicts the server already
 * reached. What is worth pinning is that the arranging never loses a
 * requirement and never merges the two axes, since both would quietly
 * misreport a result that was correct when it left the backend.
 */

describe("groupByCategory", () => {
  it("keeps required and preferred apart within a category", () => {
    const groups = groupByCategory([
      requirementResult({ requirement_label: "Python", is_required: true }),
      requirementResult({ requirement_label: "Redis", is_required: false }),
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].required.map((r) => r.requirement_label)).toEqual(["Python"]);
    expect(groups[0].preferred.map((r) => r.requirement_label)).toEqual(["Redis"]);
  });

  it("drops categories the posting says nothing about", () => {
    const groups = groupByCategory([requirementResult({ category: "skill" })]);

    expect(groups.map((g) => g.category)).toEqual(["skill"]);
  });

  it("orders categories the same way every time", () => {
    const groups = groupByCategory([
      requirementResult({ category: "certification" }),
      requirementResult({ category: "skill" }),
      requirementResult({ category: "education" }),
    ]);

    expect(groups.map((g) => g.category)).toEqual(["skill", "education", "certification"]);
  });

  it("loses nothing", () => {
    const results = [
      requirementResult({ category: "skill" }),
      requirementResult({ category: "technology", is_required: false }),
      requirementResult({ category: "qualification" }),
    ];

    const total = groupByCategory(results).reduce(
      (sum, group) => sum + group.required.length + group.preferred.length,
      0,
    );

    expect(total).toBe(results.length);
  });

  it("covers every category the backend can produce", () => {
    // A category added server-side and forgotten here would silently vanish
    // from the page, taking its requirements with it.
    const oneOfEach = CATEGORY_ORDER.map((category) => requirementResult({ category }));

    expect(groupByCategory(oneOfEach)).toHaveLength(CATEGORY_ORDER.length);
  });

  it("returns nothing for a posting with no structured requirements", () => {
    expect(groupByCategory([])).toEqual([]);
  });
});

describe("axisCounts", () => {
  it("reads the server's stored counts rather than recounting", () => {
    // The server stores them precisely so they are not re-derived. A UI that
    // recounted could disagree with the number displayed beside them.
    const match = matchDetail({
      required_total: 9,
      required_matched: 4,
      required_missing: 3,
      required_uncertain: 2,
      requirement_results: [requirementResult()],
    });

    expect(axisCounts(match).required).toEqual({
      total: 9,
      matched: 4,
      missing: 3,
      uncertain: 2,
    });
  });

  it("reports zeroes rather than throwing when there is no match yet", () => {
    expect(axisCounts(undefined).required.total).toBe(0);
    expect(axisCounts(undefined).preferred.total).toBe(0);
  });
});

describe("asPercentage", () => {
  it("renders a coverage fraction as whole percent", () => {
    expect(asPercentage(0)).toBe(0);
    expect(asPercentage(0.75)).toBe(75);
    expect(asPercentage(1)).toBe(100);
  });

  it("rounds rather than truncating", () => {
    expect(asPercentage(1 / 3)).toBe(33);
    expect(asPercentage(2 / 3)).toBe(67);
  });
});

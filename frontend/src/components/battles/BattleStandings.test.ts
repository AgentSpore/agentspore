import { describe, expect, it } from "vitest";
import { BattleApproachRecord } from "@/lib/api";
import { contenderModelName, score, sortApproaches } from "./BattleStandings";

function approach(approach_key: string, wins: number, losses: number, ties: number): BattleApproachRecord {
  return { approach_key, wins, losses, ties, battles: wins + losses + ties };
}

describe("score", () => {
  it("counts a tie as half a win", () => {
    expect(score({ wins: 0, ties: 4, battles: 4 })).toBe(50);
    expect(score({ wins: 1, ties: 1, battles: 4 })).toBe(38);
  });

  it("returns the extremes for a clean record", () => {
    expect(score({ wins: 3, ties: 0, battles: 3 })).toBe(100);
    expect(score({ wins: 0, ties: 0, battles: 3 })).toBe(0);
  });

  // The caller must gate on battles > 0 and render "—"; a 0 here would read as
  // "lost every battle" for an approach that has never been measured.
  it("is only meaningful once there is a result", () => {
    expect(score({ wins: 0, ties: 0, battles: 0 })).toBe(0);
  });
});

describe("sortApproaches", () => {
  it("keeps unmeasured approaches out of the scored positions", () => {
    const sorted = sortApproaches([
      approach("unmeasured", 0, 0, 0),
      approach("weak", 1, 3, 0),
      approach("strong", 3, 1, 0),
    ]);
    expect(sorted.map((a) => a.approach_key)).toEqual(["strong", "weak", "unmeasured"]);
  });

  it("ranks a perfect record above an unmeasured one even at equal score", () => {
    const sorted = sortApproaches([approach("unmeasured", 0, 0, 0), approach("all_losses", 0, 2, 0)]);
    expect(sorted.map((a) => a.approach_key)).toEqual(["all_losses", "unmeasured"]);
  });

  it("does not mutate the input", () => {
    const input = [approach("unmeasured", 0, 0, 0), approach("strong", 2, 0, 0)];
    sortApproaches(input);
    expect(input.map((a) => a.approach_key)).toEqual(["unmeasured", "strong"]);
  });
});

describe("contenderModelName", () => {
  it("takes the head of a seeded 'model · approach' display name", () => {
    expect(contenderModelName({ display_name: "GLM-4.6 · step by step", model_id: "glm-4.6" })).toBe("GLM-4.6");
  });

  it("falls back to model_id when the name carries no separator", () => {
    expect(contenderModelName({ display_name: "Step by step contender", model_id: "glm-4.6" })).toBe("glm-4.6");
  });

  it("keeps only the first segment of a multi-separator name", () => {
    expect(contenderModelName({ display_name: "GLM-4.6 · draft · revise", model_id: "glm-4.6" })).toBe("GLM-4.6");
  });
});

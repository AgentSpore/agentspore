import { describe, expect, it } from "vitest";
import { BattleStatus, BattleWinner } from "@/lib/api";
import { INCLUDE_UNDECIDED_PARAM, countUndecided, isUndecided } from "./undecided";

function battle(status: BattleStatus, winner: BattleWinner | null) {
  return { status, winner };
}

describe("isUndecided", () => {
  it("holds only for a completed battle with no winner", () => {
    expect(isUndecided(battle("completed", null))).toBe(true);
    expect(isUndecided(battle("completed", "a"))).toBe(false);
    expect(isUndecided(battle("completed", "tie"))).toBe(false);
  });

  // A running battle has no winner yet and an aborted challenge never will,
  // but neither is a battle that finished without a verdict — counting them
  // would put a number next to the toggle that the list does not contain.
  it("does not claim an unfinished or abandoned battle is undecided", () => {
    expect(isUndecided(battle("running", null))).toBe(false);
    expect(isUndecided(battle("judging", null))).toBe(false);
    expect(isUndecided(battle("aborted", null))).toBe(false);
    expect(isUndecided(battle("declined", null))).toBe(false);
    expect(isUndecided(battle("expired", null))).toBe(false);
  });
});

describe("countUndecided", () => {
  it("counts only the undecided rows in a mixed list", () => {
    expect(
      countUndecided([battle("completed", null), battle("completed", "b"), battle("running", null), battle("completed", null)])
    ).toBe(2);
  });

  it("is zero for an empty list", () => {
    expect(countUndecided([])).toBe(0);
  });
});

// The parameter name is the whole contract with GET /api/v1/battles: a wrong
// one returns the default list with no error, so the toggle would look alive
// and do nothing.
describe("INCLUDE_UNDECIDED_PARAM", () => {
  it("matches the query parameter the battles list route declares", () => {
    expect(INCLUDE_UNDECIDED_PARAM).toBe("include_undecided");
  });
});

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { isRecusedBattle, isVoidBattle } from "./BattleTimeline";

// The verdict_reason strings below are the ones the backend actually writes.
// They are copied verbatim from:
//   backend/app/services/battle_judges.py  -> RECUSED_PANEL_REASON
//   backend/app/services/battle_runner.py  -> settle_silent_forfeit's forced verdict
// The first test in each block re-reads that source, so a drift on either side
// fails here instead of silently downgrading the UI to "finished without quorum".
const RECUSED_REASON = "recused: no judge model free of conflict with a contender; no result is recorded";
const VOID_REASON = "void: the provider could not be reached for side(s) agent-b; no result is recorded";

function backendSource(relative: string): string {
  return readFileSync(fileURLToPath(new URL(`../../../../backend/app/services/${relative}`, import.meta.url)), "utf8");
}

/** Both literals are split across lines in Python; match the half carrying the prefix. */
function assertBackendEmits(file: string, head: string) {
  expect(backendSource(file), `${file} no longer emits: ${head}`).toContain(head);
}

describe("isVoidBattle", () => {
  it("matches the literal battle_runner.settle_silent_forfeit writes", () => {
    assertBackendEmits("battle_runner.py", "void: the provider could not be reached for side(s) ");
    expect(isVoidBattle({ winner: null, verdict_reason: VOID_REASON })).toBe(true);
  });

  it("does not claim a genuine no-quorum battle was void", () => {
    expect(isVoidBattle({ winner: null, verdict_reason: "no quorum: judges did not agree" })).toBe(false);
    expect(isVoidBattle({ winner: null, verdict_reason: RECUSED_REASON })).toBe(false);
    expect(isVoidBattle({ winner: null, verdict_reason: null })).toBe(false);
  });

  it("ignores the reason once a winner exists", () => {
    expect(isVoidBattle({ winner: "agent-a", verdict_reason: VOID_REASON })).toBe(false);
  });
});

describe("isRecusedBattle", () => {
  it("matches the literal battle_judges.RECUSED_PANEL_REASON carries", () => {
    assertBackendEmits("battle_judges.py", "recused: no judge model free of conflict with a contender; ");
    expect(isRecusedBattle({ winner: null, verdict_reason: RECUSED_REASON })).toBe(true);
  });

  it("does not claim a void or no-quorum battle was recused", () => {
    expect(isRecusedBattle({ winner: null, verdict_reason: VOID_REASON })).toBe(false);
    expect(isRecusedBattle({ winner: null, verdict_reason: "no quorum: judges did not agree" })).toBe(false);
  });

  it("ignores the reason once a winner exists", () => {
    expect(isRecusedBattle({ winner: "agent-b", verdict_reason: RECUSED_REASON })).toBe(false);
  });
});

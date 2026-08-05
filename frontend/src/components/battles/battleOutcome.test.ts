import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
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

/**
 * Walk up to the repo root instead of a fixed depth: the frontend ships as its
 * own Docker context with no backend/ beside it, and a hardcoded path would
 * fail there as ENOENT — a missing file dressed up as a drift.
 */
function findBackendServices(): string | null {
  let dir = fileURLToPath(new URL(".", import.meta.url));
  for (let up = 0; up < 8; up += 1) {
    const candidate = join(dir, "backend", "app", "services");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

const BACKEND_SERVICES = findBackendServices();

/** Both literals are split across lines in Python; match the half carrying the prefix. */
function assertBackendEmits(file: string, head: string) {
  if (BACKEND_SERVICES === null) throw new Error("backend sources not found — this block should have been skipped");
  const source = readFileSync(join(BACKEND_SERVICES, file), "utf8");
  expect(source, `${file} no longer emits: ${head}`).toContain(head);
}

// The drift guard needs the backend sources; a frontend-only checkout reports
// these as skipped rather than green, so an absent backend can never read as a
// passing contract.
describe.skipIf(BACKEND_SERVICES === null)("backend verdict_reason contract", () => {
  it("battle_runner.settle_silent_forfeit still writes the void prefix", () => {
    assertBackendEmits("battle_runner.py", "void: the provider could not be reached for side(s) ");
  });

  it("battle_judges.RECUSED_PANEL_REASON still carries the recused prefix", () => {
    assertBackendEmits("battle_judges.py", "recused: no judge model free of conflict with a contender; ");
  });
});

describe("isVoidBattle", () => {
  it("matches the reason battle_runner.settle_silent_forfeit writes", () => {
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
  it("matches the reason battle_judges.RECUSED_PANEL_REASON carries", () => {
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

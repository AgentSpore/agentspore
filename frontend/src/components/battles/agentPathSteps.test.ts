import { describe, expect, it } from "vitest";
import { BattleSubmissionView } from "./BattleVerdict";
import { LONG_VALUE_CHARS, agentPathSteps, isLongValue, parseStepLine } from "./agentPathSteps";

function sub(over: Partial<BattleSubmissionView>): BattleSubmissionView {
  return {
    side: "a",
    seq_no: 1,
    is_final: false,
    truncated: false,
    error: null,
    received_at: "2026-08-09T00:00:00Z",
    tokens_used: null,
    content: null,
    content_withheld: false,
    ...over,
  };
}

describe("parseStepLine", () => {
  it("parses a tool_call line", () => {
    expect(parseStepLine('[tool_call] read_file({"path": "a.py"})')).toEqual({
      kind: "tool_call",
      tool: "read_file",
      args: '{"path": "a.py"}',
    });
  });

  it("parses a tool_result line", () => {
    expect(parseStepLine("[tool_result] read_file -> file contents here")).toEqual({
      kind: "tool_result",
      tool: "read_file",
      output: "file contents here",
    });
  });

  it("falls back to raw text for an unrecognized format", () => {
    expect(parseStepLine("the agent is thinking about the task")).toEqual({
      kind: "raw",
      text: "the agent is thinking about the task",
    });
  });
});

describe("agentPathSteps", () => {
  it("returns only non-final rows for the given side, ordered by seq_no", () => {
    const submissions = [
      sub({ side: "a", seq_no: 2, content: "step 2" }),
      sub({ side: "b", seq_no: 1, content: "wrong side" }),
      sub({ side: "a", seq_no: 1, content: "step 1" }),
      sub({ side: "a", seq_no: 3, is_final: true, content: "final" }),
    ];
    expect(agentPathSteps(submissions, "a").map((s) => s.seq_no)).toEqual([1, 2]);
  });

  it("is empty for a single-row (non-agentic) side", () => {
    const submissions = [sub({ side: "a", seq_no: 1, is_final: true, content: "one answer" })];
    expect(agentPathSteps(submissions, "a")).toEqual([]);
  });
});

describe("isLongValue", () => {
  it("leaves an ordinary argument alone", () => {
    expect(isLongValue('{"path": "notes.md"}')).toBe(false);
  });

  it("collapses a value that carries a whole file", () => {
    // The measured case: one write_file argument was 1620 chars inside a
    // 3600-char path, so rendering it in full buries every other step.
    expect(isLongValue("x".repeat(1620))).toBe(true);
  });

  it("keeps the preview shorter than the value it stands for", () => {
    expect(LONG_VALUE_CHARS).toBeLessThan(1620);
  });
});

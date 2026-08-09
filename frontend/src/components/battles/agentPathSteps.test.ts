import { describe, expect, it } from "vitest";
import { BattleSubmissionView } from "./BattleVerdict";
import { agentPathSteps, parseStepLine } from "./agentPathSteps";

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

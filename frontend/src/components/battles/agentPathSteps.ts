import { BattleSubmissionView } from "./BattleVerdict";

/**
 * A step line is a tool call or its result: `[tool_call] name({args})` /
 * `[tool_result] name -> output` (agent-runner's format, see
 * SPEC_AGENTIC_BATTLES.md "Insertion point"). Anything else — including a
 * thinking block or a format the runner changes later — falls back to `raw`
 * so the trace never drops content it cannot parse.
 */
export type ParsedStep =
  | { kind: "tool_call"; tool: string; args: string }
  | { kind: "tool_result"; tool: string; output: string }
  | { kind: "raw"; text: string };

// [\s\S] instead of the `s` (dotAll) flag: the flag needs es2018+, this repo
// targets ES2017.
const TOOL_CALL_RE = /^\[tool_call\]\s+([^(]+)\(([\s\S]*)\)$/;
const TOOL_RESULT_RE = /^\[tool_result\]\s+(\S+)\s+->\s?([\s\S]*)$/;

export function parseStepLine(line: string): ParsedStep {
  const call = TOOL_CALL_RE.exec(line);
  if (call) return { kind: "tool_call", tool: call[1].trim(), args: call[2] };
  const result = TOOL_RESULT_RE.exec(line);
  if (result) return { kind: "tool_result", tool: result[1], output: result[2] };
  return { kind: "raw", text: line };
}

/**
 * Non-final rows for one side, oldest first. A single-row side (model
 * contender, user agent) has nothing to show as a "path" — that case is
 * distinguished from an agentic multi-row side by the caller checking
 * `length > 0`, not by this function, so backward compatibility needs no
 * special case here.
 */
export function agentPathSteps(submissions: BattleSubmissionView[], side: BattleSubmissionView["side"]): BattleSubmissionView[] {
  return submissions.filter((s) => s.side === side && !s.is_final).sort((a, b) => a.seq_no - b.seq_no);
}

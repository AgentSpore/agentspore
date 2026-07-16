"use client";

import { useEffect, useState } from "react";
import { API_URL, BattleDetail } from "@/lib/api";

// ── Local types ─────────────────────────────────────────────────────────────
// GET /battles/{id}/judgements and /battles/{id}/submissions exist on the
// backend (backend/app/api/v1/battles.py) but the shared frontend API layer
// (frontend/src/lib/api.ts) does not type them — colocated here instead of
// touching a file outside this feature's scope. Field names mirror
// backend/app/schemas/battles.py: BattleSubmissionView, BattleJudgeRunView,
// BattleJudgementView, JudgeTally, BattleVerdictView.

type Side = "a" | "b";
type Vote = "a" | "b" | "tie" | "abstain" | "error";
type PresentedOrder = "ab" | "ba";
type JudgeKind = "llm" | "human";
type JudgeRunStatus = "pending" | "running" | "completed" | "failed";

interface BattleSubmissionView {
  side: Side;
  seq_no: number;
  is_final: boolean;
  truncated: boolean;
  error: string | null;
  received_at: string;
  tokens_used: number | null;
  content: string | null;
  content_withheld: boolean;
}

interface BattleJudgeRunView {
  judge_kind: JudgeKind;
  judge_ref: string;
  replicate_seed: string;
  presented_order: PresentedOrder;
  status: JudgeRunStatus;
  vote: Vote | null;
  confidence: number | null;
  reasoning: string | null;
  scores: Record<string, unknown> | null;
}

interface BattleJudgementView {
  judge_kind: JudgeKind;
  judge_ref: string;
  replicate_seed: string;
  vote: Vote;
  confidence: number | null;
  reasoning: string | null;
  scores: Record<string, unknown> | null;
  position_sensitive: boolean;
}

interface JudgeTally {
  votes_for_a: number;
  votes_for_b: number;
  ties: number;
  abstained: number;
  errored: number;
  valid: number;
  position_sensitive: number;
}

interface BattleVerdictView {
  judgements: BattleJudgementView[];
  runs: BattleJudgeRunView[];
  tallies: Record<string, JudgeTally>;
}

// Battles that have stopped taking turns — submissions carry real content
// from this point on (mirrors backend _TURNS_CLOSED).
const CONTENT_VISIBLE_STATES = new Set(["judging", "completed"]);

function voteLabel(vote: Vote | null): string {
  switch (vote) {
    case "a":
      return "за A";
    case "b":
      return "за B";
    case "tie":
      return "ничья";
    case "abstain":
      return "воздержалась";
    case "error":
      return "ошибка судьи";
    default:
      return "нет ответа";
  }
}

function sideName(side: Side, agentAName: string, agentBName: string): string {
  return side === "a" ? agentAName : agentBName;
}

interface Props {
  battle: BattleDetail;
  agentAName: string;
  agentBName: string;
}

/**
 * Verdict evidence — both final answers, the collapsed replicate votes
 * («реплики» — three replicates of ONE model, never «три судьи»), the raw
 * judge runs (position-bias control, collapsible), and the tallies.
 *
 * Fetches only while the battle has stopped taking turns (judging/completed):
 * the submissions endpoint withholds content earlier, and the judgements
 * endpoint returns empty collections before completion.
 */
export function BattleVerdictEvidence({ battle, agentAName, agentBName }: Props) {
  const [submissions, setSubmissions] = useState<BattleSubmissionView[]>([]);
  const [verdict, setVerdict] = useState<BattleVerdictView | null>(null);
  const [showRuns, setShowRuns] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const visible = CONTENT_VISIBLE_STATES.has(battle.status);

  useEffect(() => {
    if (!visible) return;
    let alive = true;

    Promise.all([
      fetch(`${API_URL}/api/v1/battles/${battle.id}/submissions`).then((r) =>
        r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))
      ),
      fetch(`${API_URL}/api/v1/battles/${battle.id}/judgements`).then((r) =>
        r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))
      ),
    ])
      .then(([subs, verd]: [BattleSubmissionView[], BattleVerdictView]) => {
        if (!alive) return;
        setSubmissions(subs);
        setVerdict(verd);
        setErr(null);
      })
      .catch((e) => {
        if (alive) setErr(e instanceof Error ? e.message : "не удалось загрузить доказательства вердикта");
      });

    return () => {
      alive = false;
    };
  }, [battle.id, battle.status, visible]);

  if (!visible) return null;

  const finalBySide: Partial<Record<Side, BattleSubmissionView>> = {};
  for (const s of submissions) {
    if (s.is_final || !finalBySide[s.side]) finalBySide[s.side] = s;
  }

  const llmJudgements = verdict?.judgements.filter((j) => j.judge_kind === "llm") ?? [];
  const humanJudgements = verdict?.judgements.filter((j) => j.judge_kind === "human") ?? [];
  const llmTally = verdict?.tallies["llm"];
  const humanTally = verdict?.tallies["human"];

  return (
    <div className="mt-4 space-y-4">
      {err && <div className="text-xs text-amber-400">{err}</div>}

      {/* Final answers */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(["a", "b"] as const).map((side) => {
          const sub = finalBySide[side];
          return (
            <div key={side} className="rounded-lg border border-neutral-800 bg-neutral-900/30 p-4">
              <div className={`text-xs uppercase mb-2 ${side === "a" ? "text-violet-400" : "text-cyan-400"}`}>
                Ответ {sideName(side, agentAName, agentBName)}
              </div>
              {!sub ? (
                <div className="text-sm text-neutral-500">ответа не поступило</div>
              ) : sub.content_withheld ? (
                <div className="text-sm text-neutral-500">содержимое скрыто до конца боя</div>
              ) : sub.error ? (
                <div className="text-sm text-red-400">ошибка при генерации: {sub.error}</div>
              ) : (
                <div className="text-sm text-neutral-300 whitespace-pre-wrap">
                  {sub.content}
                  {sub.truncated && <span className="text-amber-400"> (обрезано по лимиту)</span>}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Collapsed replicate votes */}
      {llmJudgements.length > 0 && (
        <div className="rounded-lg border border-neutral-800 bg-neutral-900/30 p-4">
          <div className="text-xs uppercase text-neutral-500 mb-3">
            Реплики LLM-судьи ({llmJudgements.length} реплики одной модели)
          </div>
          <div className="space-y-2">
            {llmJudgements.map((j) => (
              <div key={j.replicate_seed} className="rounded border border-neutral-800 p-2.5 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-neutral-300">
                    Реплика {j.replicate_seed.slice(0, 8)} — {voteLabel(j.vote)}
                  </span>
                  {j.confidence !== null && (
                    <span className="text-xs text-neutral-500">увер.: {j.confidence.toFixed(2)}</span>
                  )}
                </div>
                {j.reasoning && <div className="text-xs text-neutral-500 mt-1">{j.reasoning}</div>}
                {j.position_sensitive && (
                  <div className="text-xs text-amber-400 mt-1">
                    чувствительна к порядку предъявления A/B
                  </div>
                )}
              </div>
            ))}
          </div>
          {llmTally && (
            <div className="text-xs text-neutral-500 mt-3">
              За {agentAName}: {llmTally.votes_for_a} · за {agentBName}: {llmTally.votes_for_b} · ничьи:{" "}
              {llmTally.ties} · воздержались: {llmTally.abstained} · ошибок: {llmTally.errored} · в кворуме:{" "}
              {llmTally.valid}
            </div>
          )}
        </div>
      )}

      {humanJudgements.length > 0 && (
        <div className="rounded-lg border border-neutral-800 bg-neutral-900/30 p-4">
          <div className="text-xs uppercase text-neutral-500 mb-3">Человеческие голоса</div>
          <div className="space-y-2">
            {humanJudgements.map((j) => (
              <div key={j.replicate_seed} className="rounded border border-neutral-800 p-2.5 text-sm">
                <span className="text-neutral-300">{voteLabel(j.vote)}</span>
                {j.reasoning && <div className="text-xs text-neutral-500 mt-1">{j.reasoning}</div>}
              </div>
            ))}
          </div>
          {humanTally && (
            <div className="text-xs text-neutral-500 mt-3">
              За {agentAName}: {humanTally.votes_for_a} · за {agentBName}: {humanTally.votes_for_b} · ничьи:{" "}
              {humanTally.ties} · воздержались: {humanTally.abstained} · ошибок: {humanTally.errored} · в
              кворуме: {humanTally.valid}
            </div>
          )}
        </div>
      )}

      {/* Raw runs — collapsible evidence for the position-bias control */}
      {verdict && verdict.runs.length > 0 && (
        <div className="rounded-lg border border-neutral-800 bg-neutral-900/20 p-4">
          <button
            onClick={() => setShowRuns((v) => !v)}
            className="text-xs uppercase text-neutral-500 hover:text-neutral-300 transition"
          >
            {showRuns ? "Скрыть" : "Показать"} сырые прогоны судейства ({verdict.runs.length})
          </button>
          {showRuns && (
            <div className="mt-3 space-y-2">
              {verdict.runs.map((run, i) => (
                <div key={`${run.replicate_seed}-${run.presented_order}-${i}`} className="rounded border border-neutral-800 p-2.5 text-xs">
                  <div className="flex items-center justify-between gap-2 text-neutral-400">
                    <span>
                      реплика {run.replicate_seed.slice(0, 8)} · порядок {run.presented_order.toUpperCase()} ·
                      статус {run.status}
                    </span>
                    <span>{voteLabel(run.vote)}</span>
                  </div>
                  {run.reasoning && <div className="text-neutral-500 mt-1">{run.reasoning}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

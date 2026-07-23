"use client";

import { useEffect, useState } from "react";
import { API_URL, BattleDetail, BattleSide } from "@/lib/api";
import { AgentIdentity } from "@/components/battles/AgentIdentity";
import { Disclosure } from "@/components/battles/Disclosure";
import { SIDE_ACCENT, SectionHead, eloDeltaText } from "@/components/battles/battleUi";

// ── Local types ─────────────────────────────────────────────────────────────
// GET /battles/{id}/judgements and /battles/{id}/submissions exist on the
// backend (backend/app/api/v1/battles.py) but the shared frontend API layer
// (frontend/src/lib/api.ts) does not type them — colocated here instead of
// touching a file outside this feature's scope. Field names mirror
// backend/app/schemas/battles.py: BattleSubmissionView, BattleJudgeRunView,
// BattleJudgementView, JudgeTally, BattleVerdictView.

export type Vote = "a" | "b" | "tie" | "abstain" | "error";
type PresentedOrder = "ab" | "ba";
type JudgeKind = "llm" | "human";
type JudgeRunStatus = "pending" | "running" | "completed" | "failed";

export interface BattleSubmissionView {
  side: BattleSide;
  seq_no: number;
  is_final: boolean;
  truncated: boolean;
  error: string | null;
  received_at: string;
  tokens_used: number | null;
  content: string | null;
  content_withheld: boolean;
}

export interface BattleJudgeRunView {
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

export interface BattleJudgementView {
  judge_kind: JudgeKind;
  judge_ref: string;
  replicate_seed: string;
  vote: Vote;
  confidence: number | null;
  reasoning: string | null;
  scores: Record<string, unknown> | null;
  position_sensitive: boolean;
}

export interface JudgeTally {
  votes_for_a: number;
  votes_for_b: number;
  ties: number;
  abstained: number;
  errored: number;
  valid: number;
  position_sensitive: number;
}

export interface BattleVerdictView {
  judgements: BattleJudgementView[];
  runs: BattleJudgeRunView[];
  tallies: Record<string, JudgeTally>;
}

// Battles that have stopped taking turns — submissions carry real content
// from this point on (mirrors backend _TURNS_CLOSED).
export const CONTENT_VISIBLE_STATES = new Set(["judging", "completed"]);

const VOTE_META: Record<Vote, { label: string; classes: string }> = {
  a: { label: "For A", classes: "bg-violet-500/10 text-violet-300 border-violet-500/30" },
  b: { label: "For B", classes: "bg-cyan-500/10 text-cyan-300 border-cyan-500/30" },
  tie: { label: "Tie", classes: "bg-neutral-500/10 text-neutral-400 border-neutral-500/30" },
  abstain: { label: "Abstained", classes: "bg-amber-500/10 text-amber-300 border-amber-500/30" },
  error: { label: "Replica error", classes: "bg-neutral-500/10 text-rose-400 border-neutral-700" },
};

export function VoteChip({
  vote,
  agentAName,
  agentBName,
}: {
  vote: Vote | null;
  agentAName?: string;
  agentBName?: string;
}) {
  if (!vote) {
    return (
      <span className="inline-flex items-center rounded-md border border-neutral-700 px-2 py-0.5 text-xs text-neutral-500">
        no response
      </span>
    );
  }
  const meta = VOTE_META[vote];
  const label =
    vote === "a" && agentAName ? `For ${agentAName}` : vote === "b" && agentBName ? `For ${agentBName}` : meta.label;
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${meta.classes}`}>
      {label}
    </span>
  );
}

function pluralReplicas(n: number): string {
  return n === 1 ? "replica" : "replicas";
}

/**
 * The backend's verdict_reason is a machine string for engineers (see
 * backend/app/services/battle_judges.py — "2 for alpha-side, 1 tie (3
 * replicates)" / "no quorum: 1 valid of 2 required (1 errored, 1 abstained)"),
 * never shown to users as-is. This composes the honest sentence
 * client-side from the same tally the raw string summarizes, so the
 * human-readable line and the tallies below it can never drift out of sync.
 */
function composeQuorumSummary(tally: JudgeTally, agentAName: string, agentBName: string): string {
  const { votes_for_a, votes_for_b, ties, abstained, errored, valid } = tally;
  const clauses: string[] = [];

  if (valid === 0) {
    clauses.push("LLM quorum: no valid votes");
  } else if (votes_for_a > votes_for_b) {
    clauses.push(`LLM quorum: majority ${votes_for_a} of ${valid} valid votes for ${agentAName}`);
  } else if (votes_for_b > votes_for_a) {
    clauses.push(`LLM quorum: majority ${votes_for_b} of ${valid} valid votes for ${agentBName}`);
  } else {
    clauses.push(`LLM quorum: votes split evenly (${votes_for_a} to ${votes_for_b} of ${valid})`);
  }

  if (ties > 0) {
    clauses.push(`${ties} ${pluralReplicas(ties)} called a tie`);
  }
  if (abstained > 0) {
    clauses.push(`${abstained} ${pluralReplicas(abstained)} abstained`);
  }
  if (errored > 0) {
    clauses.push(`${errored} ${pluralReplicas(errored)} errored out`);
  }

  return clauses.join("; ") + ".";
}

// ── Confidence meter ────────────────────────────────────────────────────────

// Pre-built width classes snapped to 5% steps — Tailwind only generates
// classes it can see statically, and the hard rule for this feature bans
// inline style=, so a dynamic `width: N%` is not an option. Snapping to the
// nearest 5% is visually indistinguishable on a 1px-tall meter.
const WIDTH_STEPS = [
  "w-[0%]", "w-[5%]", "w-[10%]", "w-[15%]", "w-[20%]", "w-[25%]", "w-[30%]", "w-[35%]", "w-[40%]", "w-[45%]",
  "w-[50%]", "w-[55%]", "w-[60%]", "w-[65%]", "w-[70%]", "w-[75%]", "w-[80%]", "w-[85%]", "w-[90%]", "w-[95%]", "w-[100%]",
] as const;

function widthStep(pct: number): string {
  const idx = Math.min(20, Math.max(0, Math.round(pct / 5)));
  return WIDTH_STEPS[idx];
}

function ConfidenceMeter({ confidence, vote }: { confidence: number | null; vote: Vote }) {
  if (confidence === null) {
    return (
      <div className="mt-2.5">
        <div className="font-mono text-[13px] text-neutral-500">
          — <span className="font-sans text-[11px] text-neutral-600">confidence</span>
        </div>
        <div className="mt-1.5 h-1 w-full rounded-full bg-neutral-800 overflow-hidden" />
      </div>
    );
  }
  const pct = Math.round(Math.min(1, Math.max(0, confidence)) * 100);
  const fill = vote === "a" ? SIDE_ACCENT.a.meter : vote === "b" ? SIDE_ACCENT.b.meter : "bg-neutral-500";
  return (
    <div className="mt-2.5">
      <div className={`font-mono tabular-nums text-[13px] ${vote === "abstain" ? "text-neutral-500" : "text-neutral-300"}`}>
        {pct}% <span className="font-sans text-[11px] text-neutral-600">confidence</span>
      </div>
      <div
        className="mt-1.5 h-1 w-full rounded-full bg-neutral-800 overflow-hidden"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Confidence"
      >
        <div className={`h-full rounded-full ${fill} ${widthStep(pct)}`} />
      </div>
    </div>
  );
}

// ── Replica card (shared shape: completed verdict + judging progress) ──────

export function ReplicaCard({
  index,
  vote,
  confidence,
  reasoning,
  positionSensitive,
  pending,
  agentAName,
  agentBName,
}: {
  index: number;
  vote: Vote | null;
  confidence: number | null;
  reasoning?: string | null;
  positionSensitive?: boolean;
  /** Judging state — the run has not landed yet. */
  pending?: boolean;
  agentAName: string;
  agentBName: string;
}) {
  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-900/30 p-3.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-neutral-500 font-mono">Replica {index + 1}</span>
        {pending ? (
          <span className="inline-flex items-center gap-1.5 rounded-md border border-neutral-700 px-2 py-0.5 text-xs text-neutral-500">
            <span className="relative flex h-1.5 w-1.5">
              <span className="battle-live-dot-ping absolute inline-flex h-full w-full rounded-full bg-current opacity-60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
            </span>
            run in progress
          </span>
        ) : (
          <VoteChip vote={vote} agentAName={agentAName} agentBName={agentBName} />
        )}
      </div>
      <ConfidenceMeter confidence={pending ? null : confidence} vote={vote ?? "abstain"} />
      {reasoning && (
        <div className="mt-3 border-t border-neutral-800 pt-3 text-[13px] leading-[1.6] text-neutral-300">
          {reasoning}
        </div>
      )}
      {positionSensitive && (
        <div className="mt-2.5 flex gap-1.5 flex-wrap">
          <span className="inline-flex items-center rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-300">
            Order-sensitive (A/B)
          </span>
        </div>
      )}
    </div>
  );
}

// ── Tally ───────────────────────────────────────────────────────────────────

function TallyLine({ tally, agentAName, agentBName }: { tally: JudgeTally; agentAName: string; agentBName: string }) {
  const items: { label: string; value: number; tone: string }[] = [
    { label: `for ${agentAName}`, value: tally.votes_for_a, tone: "text-violet-300" },
    { label: `for ${agentBName}`, value: tally.votes_for_b, tone: "text-cyan-300" },
    { label: "ties", value: tally.ties, tone: "text-neutral-400" },
    { label: "abstained", value: tally.abstained, tone: "text-amber-300" },
    { label: "errors", value: tally.errored, tone: "text-rose-400" },
    { label: "in quorum", value: tally.valid, tone: "text-neutral-300" },
  ];
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1.5 mt-3.5 pt-3.5 border-t border-neutral-800">
      {items.map((it) => (
        <div key={it.label} className="text-xs">
          <span className={`font-mono font-medium ${it.tone}`}>{it.value}</span>{" "}
          <span className="text-neutral-600">{it.label}</span>
        </div>
      ))}
    </div>
  );
}

// ── Final answers ───────────────────────────────────────────────────────────

function FinalAnswer({
  battle,
  side,
  sub,
  name,
}: {
  battle: BattleDetail;
  side: BattleSide;
  sub: BattleSubmissionView | undefined;
  name: string;
}) {
  const isWinner = battle.winner === side;
  const accent = SIDE_ACCENT[side];
  return (
    <div className={`rounded-lg border p-4 ${accent.answer}`}>
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <AgentIdentity side={side} agentId={side === "a" ? battle.agent_a_id : battle.agent_b_id} name={name} size="sm" />
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] font-mono uppercase tracking-[0.08em] text-neutral-600 border border-neutral-700 rounded px-1.5 py-0.5">
            Final answer
          </span>
          {isWinner && (
            <span className="text-[10px] font-mono uppercase tracking-[0.08em] text-emerald-300 border border-emerald-500/30 bg-emerald-500/10 rounded px-1.5 py-0.5">
              Winner
            </span>
          )}
        </div>
      </div>
      {!sub ? (
        <div className="text-sm text-neutral-500">No final answer received</div>
      ) : sub.content_withheld ? (
        <div className="text-sm text-neutral-500 italic">Content hidden until the battle ends</div>
      ) : sub.error ? (
        <div className="rounded-md border border-neutral-800 bg-neutral-950/40 px-3 py-2 text-sm text-neutral-400 flex items-center gap-2">
          <span className="text-[10px] font-mono uppercase tracking-wide text-rose-400 border border-neutral-700 rounded px-1.5 py-0.5 shrink-0">
            Generation error
          </span>
        </div>
      ) : (
        <>
          {/* Clamped to ~10 lines (15rem at text-[13px]/1.65) — prod answers
              can exceed 400 words, so the full text lives behind an expander
              instead of pushing the replicas below the fold. */}
          <div className="text-[13px] leading-[1.65] text-neutral-200 whitespace-pre-wrap max-h-[15rem] overflow-y-auto">
            {sub.content}
          </div>
          {sub.truncated && <div className="text-xs text-amber-400 mt-2">Reply truncated at the limit</div>}
        </>
      )}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

interface Props {
  battle: BattleDetail;
  agentAName: string;
  agentBName: string;
}

/**
 * "Battle outcome" — the completed-state verdict panel: winner announcement (or the
 * real no-quorum outcome), Elo as a right column at ≥lg, both final answers,
 * jury replica votes (three independent jury replicas, never framed as
 * "judges"), the raw judge runs (position-bias control, collapsible), and the
 * tallies — one dense result block per the approved mockup.
 *
 * No-quorum is a REAL completed state (winner NULL), rendered as the outcome
 * of the battle — "outcome undetermined: the jury did not reach quorum" — never as
 * a footnote and never as a "tie".
 *
 * Fetches only once the battle has stopped taking turns (judging/completed):
 * the submissions endpoint withholds content earlier, and the judgements
 * endpoint returns empty collections before completion.
 */
export function BattleVerdict({ battle, agentAName, agentBName }: Props) {
  const [submissions, setSubmissions] = useState<BattleSubmissionView[]>([]);
  const [verdict, setVerdict] = useState<BattleVerdictView | null>(null);
  const [loaded, setLoaded] = useState(false);
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
        if (alive) setErr(e instanceof Error ? e.message : "failed to load verdict evidence");
      })
      .finally(() => {
        if (alive) setLoaded(true);
      });

    return () => {
      alive = false;
    };
  }, [battle.id, battle.status, visible]);

  if (!visible) return null;

  const finalBySide: Partial<Record<BattleSide, BattleSubmissionView>> = {};
  for (const s of submissions) {
    if (s.is_final || !finalBySide[s.side]) finalBySide[s.side] = s;
  }

  const llmJudgements = verdict?.judgements.filter((j) => j.judge_kind === "llm") ?? [];
  const humanJudgements = verdict?.judgements.filter((j) => j.judge_kind === "human") ?? [];
  const llmTally = verdict?.tallies["llm"];
  const humanTally = verdict?.tallies["human"];

  const runsBySeed = new Map<string, BattleJudgeRunView[]>();
  for (const run of verdict?.runs ?? []) {
    const arr = runsBySeed.get(run.replicate_seed) ?? [];
    arr.push(run);
    runsBySeed.set(run.replicate_seed, arr);
  }

  const isCompleted = battle.status === "completed";
  const noQuorum = isCompleted && battle.winner === null;
  const winnerName =
    battle.winner === "a" ? agentAName : battle.winner === "b" ? agentBName : null;
  const eloA = eloDeltaText(battle.elo_a_before, battle.elo_a_after);
  const eloB = eloDeltaText(battle.elo_b_before, battle.elo_b_after);

  if (!isCompleted) return null;

  return (
    <section aria-label="Battle outcome" className="battle-verdict-enter">
      {err && (
        <div className="mb-4 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300">{err}</div>
      )}

      {!loaded && (
        <div className="rounded-2xl border border-neutral-800 bg-neutral-900/30 p-6 space-y-2" aria-hidden="true">
          <div className="animate-pulse bg-neutral-800/50 rounded-md h-3.5 w-full" />
          <div className="animate-pulse bg-neutral-800/50 rounded-md h-3.5 w-[88%]" />
          <div className="animate-pulse bg-neutral-800/50 rounded-md h-3.5 w-[62%]" />
        </div>
      )}

      {loaded && (
        <div
          className={`overflow-hidden rounded-2xl border bg-neutral-900/45 shadow-[0_16px_48px_rgba(0,0,0,0.28)] ${
            noQuorum ? "border-amber-500/25" : "border-emerald-500/25"
          }`}
        >
          {/* 1. Verdict + Elo — dense two-column at ≥lg: the verdict reason on
              the left, the Elo change as a right column. */}
          <div className="grid lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="p-5 sm:p-6">
              <div
                className={`text-[11px] font-mono uppercase tracking-[0.12em] mb-2.5 ${
                  noQuorum ? "text-amber-400" : "text-emerald-400"
                }`}
              >
                Verdict
              </div>
              {winnerName ? (
                <>
                  <div className="text-xs text-neutral-500 mb-1">Winner</div>
                  <div
                    className={`text-[26px] leading-8 font-semibold tracking-[-0.025em] ${
                      battle.winner === "a" ? SIDE_ACCENT.a.text : SIDE_ACCENT.b.text
                    }`}
                  >
                    {winnerName}
                  </div>
                  <div className="mt-2.5 flex gap-2 flex-wrap">
                    <span className="inline-flex items-center rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-300">
                      Side {(battle.winner as string).toUpperCase()}
                    </span>
                    {llmTally && (
                      <span className="inline-flex items-center rounded-md border border-violet-500/30 bg-violet-500/10 px-2 py-0.5 text-xs font-medium text-violet-300">
                        Quorum: {llmTally.valid} of {llmJudgements.length || llmTally.valid}
                      </span>
                    )}
                  </div>
                </>
              ) : battle.winner === "tie" ? (
                <>
                  <div className="text-[26px] leading-8 font-semibold tracking-[-0.025em] text-neutral-200">Tie</div>
                  <div className="mt-2.5 flex gap-2 flex-wrap">
                    <span className="inline-flex items-center rounded-md border border-neutral-700 px-2 py-0.5 text-xs font-medium text-neutral-400">
                      votes split evenly
                    </span>
                  </div>
                </>
              ) : (
                <>
                  <div className="text-xs text-neutral-500 mb-1">Outcome</div>
                  <div className="text-[22px] leading-7 sm:text-[26px] sm:leading-8 font-semibold tracking-[-0.025em] text-amber-300">
                    Outcome undetermined: the jury did not reach quorum
                  </div>
                  <p className="text-[13px] text-neutral-400 mt-2">
                    Elo does not change; the battle is marked completed with no winner.
                  </p>
                </>
              )}
              {llmTally && (
                <p className="text-sm text-neutral-300 leading-[1.6] max-w-[70ch] mt-3">
                  {composeQuorumSummary(llmTally, agentAName, agentBName)}
                </p>
              )}
              {battle.verdict_reason && (
                <p className="mt-2.5 max-w-[70ch] font-mono text-xs text-neutral-600">
                  <span className="mr-1.5 uppercase tracking-[0.08em] text-neutral-700">Technical verdict:</span>
                  {battle.verdict_reason}
                </p>
              )}
            </div>

            {/* Elo — right column at ≥lg, two-cell strip below that. */}
            <div className="grid grid-cols-2 lg:grid-cols-1 border-t lg:border-t-0 lg:border-l border-neutral-800">
              {(["a", "b"] as const).map((side) => {
                const before = side === "a" ? battle.elo_a_before : battle.elo_b_before;
                const after = side === "a" ? battle.elo_a_after : battle.elo_b_after;
                const delta = side === "a" ? eloA : eloB;
                const name = side === "a" ? agentAName : agentBName;
                return (
                  <div
                    key={side}
                    className={`p-4 sm:px-6 lg:py-5 flex flex-col justify-center ${
                      side === "b" ? "border-l lg:border-l-0 lg:border-t border-neutral-800" : ""
                    }`}
                  >
                    <div className={`text-xs font-medium truncate ${SIDE_ACCENT[side].text}`}>{name}</div>
                    {battle.is_rated ? (
                      <div className="font-mono tabular-nums text-sm mt-1">
                        {before ?? "—"} → {after ?? "—"} <span className={delta.tone}>({delta.text})</span>
                      </div>
                    ) : (
                      <div className="font-mono text-sm mt-1 text-neutral-500">Elo unchanged</div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* 2. Final answers */}
          <div className="p-5 sm:p-6 border-t border-neutral-800">
            <SectionHead title="Final answers" note="revealed once locked in" className="mb-3.5" />
            <div className="grid md:grid-cols-2 gap-4">
              <FinalAnswer battle={battle} side="a" sub={finalBySide.a} name={agentAName} />
              <FinalAnswer battle={battle} side="b" sub={finalBySide.b} name={agentBName} />
            </div>
          </div>

          {/* 3. Jury replicas */}
          {llmJudgements.length > 0 && (
            <div className="p-5 sm:p-6 border-t border-neutral-800">
              <SectionHead title="Jury replicas" className="mb-1" />
              <p className="text-xs text-neutral-500 mb-3.5">
                Three independent jury runs; the A/B order is checked separately.
              </p>
              <div className="grid md:grid-cols-3 gap-3">
                {llmJudgements.map((j, i) => (
                  <ReplicaCard
                    key={j.replicate_seed}
                    index={i}
                    vote={j.vote}
                    confidence={j.confidence}
                    reasoning={j.reasoning}
                    positionSensitive={j.position_sensitive}
                    agentAName={agentAName}
                    agentBName={agentBName}
                  />
                ))}
              </div>
              {llmTally && <TallyLine tally={llmTally} agentAName={agentAName} agentBName={agentBName} />}
            </div>
          )}

          {/* 4. Human votes — separate tally, never merged with LLM */}
          {humanJudgements.length > 0 && (
            <div className="p-5 sm:p-6 border-t border-neutral-800">
              <SectionHead title="Human votes" className="mb-3" />
              <div className="space-y-2">
                {humanJudgements.map((j) => (
                  <div key={j.replicate_seed} className="rounded-md border border-neutral-800 p-2.5">
                    <VoteChip vote={j.vote} agentAName={agentAName} agentBName={agentBName} />
                    {j.reasoning && <div className="text-xs text-neutral-500 mt-2 leading-relaxed">{j.reasoning}</div>}
                  </div>
                ))}
              </div>
              {humanTally && <TallyLine tally={humanTally} agentAName={agentAName} agentBName={agentBName} />}
            </div>
          )}

          {/* 5. Raw runs — collapsible position-bias control, grouped by replicate */}
          {verdict && verdict.runs.length > 0 && (
            <div className="p-5 sm:p-6 border-t border-neutral-800 bg-neutral-950/40">
              <Disclosure
                label={`Technical runs · ${verdict.runs.length}`}
                openLabel={`Hide technical runs · ${verdict.runs.length}`}
                className="min-h-11 flex items-center"
              >
                <div className="space-y-3">
                  {Array.from(runsBySeed.entries()).map(([seed, runs], i) => (
                    <div key={seed} className="rounded-md border border-neutral-800 p-3 text-xs">
                      <div className="text-neutral-400 mb-2">Replica {i + 1}</div>
                      <div className="space-y-2">
                        {runs.map((run) => (
                          <div
                            key={run.presented_order}
                            className="border-t border-neutral-800/70 pt-2 first:border-t-0 first:pt-0"
                          >
                            <div className="flex items-center justify-between gap-2 flex-wrap">
                              <span className="text-neutral-400">
                                Order {run.presented_order === "ab" ? "A→B" : "B→A"} · status {run.status}
                              </span>
                              <VoteChip vote={run.vote} agentAName={agentAName} agentBName={agentBName} />
                            </div>
                            {run.confidence !== null && (
                              <div className="text-neutral-500 mt-1">Confidence: {Math.round(run.confidence * 100)}%</div>
                            )}
                            {run.reasoning && <div className="text-neutral-500 mt-1 leading-relaxed">{run.reasoning}</div>}
                          </div>
                        ))}
                      </div>
                      <div className="text-[11px] font-mono text-neutral-500 mt-2">seed {seed.slice(0, 8)}</div>
                    </div>
                  ))}
                </div>
              </Disclosure>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

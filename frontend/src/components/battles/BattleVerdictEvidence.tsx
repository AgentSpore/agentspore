"use client";

import { useEffect, useState } from "react";
import { API_URL, BattleDetail } from "@/lib/api";
import { AgentIdentity } from "@/components/battles/AgentIdentity";
import { Disclosure } from "@/components/battles/Disclosure";

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

const VOTE_META: Record<Vote, { label: string; classes: string }> = {
  a: { label: "За A", classes: "bg-violet-500/10 text-violet-300 border-violet-500/30" },
  b: { label: "За B", classes: "bg-cyan-500/10 text-cyan-300 border-cyan-500/30" },
  tie: { label: "Ничья", classes: "bg-neutral-500/10 text-neutral-400 border-neutral-500/30" },
  abstain: { label: "Воздержалась", classes: "bg-amber-500/10 text-amber-300 border-amber-500/30" },
  error: { label: "Ошибка реплики", classes: "bg-neutral-500/10 text-rose-400 border-neutral-700" },
};

function VoteChip({ vote, agentAName, agentBName }: { vote: Vote | null; agentAName?: string; agentBName?: string }) {
  if (!vote) {
    return (
      <span className="inline-flex items-center rounded-md border border-neutral-700 px-2 py-0.5 text-xs text-neutral-500">
        нет ответа
      </span>
    );
  }
  const meta = VOTE_META[vote];
  const label = vote === "a" && agentAName ? `За ${agentAName}` : vote === "b" && agentBName ? `За ${agentBName}` : meta.label;
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${meta.classes}`}>
      {label}
    </span>
  );
}

function ConfidenceBar({ confidence, vote }: { confidence: number | null; vote: Vote }) {
  if (confidence === null) return null;
  const pct = Math.round(Math.min(1, Math.max(0, confidence)) * 100);
  const fill = vote === "a" ? "bg-violet-400" : vote === "b" ? "bg-cyan-400" : "bg-neutral-500";
  return (
    <div className="mt-3 pt-3 border-t border-neutral-800">
      <div className="flex items-center justify-between text-[11px] text-neutral-500 mb-1.5">
        <span className="font-mono uppercase tracking-[0.1em]">Уверенность</span>
        <span className="font-mono tabular-nums text-neutral-400">{pct}%</span>
      </div>
      <div className="h-1 w-full rounded-full bg-neutral-800 overflow-hidden" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full rounded-full ${fill}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function TallyLine({ tally, agentAName, agentBName }: { tally: JudgeTally; agentAName: string; agentBName: string }) {
  const items: { label: string; value: number; tone: string }[] = [
    { label: `За ${agentAName}`, value: tally.votes_for_a, tone: "text-violet-300" },
    { label: `За ${agentBName}`, value: tally.votes_for_b, tone: "text-cyan-300" },
    { label: "Ничья", value: tally.ties, tone: "text-neutral-400" },
    { label: "Воздержались", value: tally.abstained, tone: "text-amber-300" },
    { label: "Ошибки", value: tally.errored, tone: "text-rose-400" },
    { label: "В кворуме", value: tally.valid, tone: "text-neutral-300" },
  ];
  return (
    <div className="grid grid-cols-2 sm:flex sm:flex-wrap gap-x-4 gap-y-1.5 mt-3 pt-3 border-t border-neutral-800">
      {items.map((it) => (
        <div key={it.label} className="text-xs">
          <span className={`font-mono font-medium ${it.tone}`}>{it.value}</span>{" "}
          <span className="text-neutral-600">{it.label}</span>
        </div>
      ))}
    </div>
  );
}

function sideName(side: Side, agentAName: string, agentBName: string): string {
  return side === "a" ? agentAName : agentBName;
}

function eloDelta(before: number | null, after: number | null): { text: string; tone: string } {
  if (before === null || after === null) return { text: "—", tone: "text-neutral-600" };
  const d = after - before;
  if (d > 0) return { text: `+${d}`, tone: "text-emerald-400" };
  if (d < 0) return { text: `${d}`, tone: "text-rose-400" };
  return { text: "0", tone: "text-neutral-500" };
}

interface Props {
  battle: BattleDetail;
  agentAName: string;
  agentBName: string;
}

/**
 * "Ход боя" feed (checkpoints, once turns are closed) plus "Итог боя" —
 * winner announcement, Elo, both final answers, replicate votes
 * («реплики» — three replicates of ONE model, never «три судьи»), the raw
 * judge runs (position-bias control, collapsible), and the tallies — all in
 * one result block per the redesign spec.
 *
 * Fetches only while the battle has stopped taking turns (judging/completed):
 * the submissions endpoint withholds content earlier, and the judgements
 * endpoint returns empty collections before completion. Running renders two
 * honest neutral tracks with no fabricated checkpoint data (no fetch).
 */
export function BattleVerdictEvidence({ battle, agentAName, agentBName }: Props) {
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
        if (alive) setErr(e instanceof Error ? e.message : "не удалось загрузить доказательства вердикта");
      })
      .finally(() => {
        if (alive) setLoaded(true);
      });

    return () => {
      alive = false;
    };
  }, [battle.id, battle.status, visible]);

  if (battle.status === "running") {
    return (
      <div className="mt-6">
        <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500 mb-3">Ход боя</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {(["a", "b"] as const).map((side) => (
            <div key={side} className="rounded-lg border border-neutral-800/80 bg-neutral-900/30 px-4 py-3 flex items-center gap-2.5">
              <span className={`h-1.5 w-1.5 rounded-full ${side === "a" ? "bg-violet-400" : "bg-cyan-400"} shrink-0`} />
              <span className="text-sm text-neutral-400">Агент работает</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (!visible) return null;

  const finalBySide: Partial<Record<Side, BattleSubmissionView>> = {};
  for (const s of submissions) {
    if (s.is_final || !finalBySide[s.side]) finalBySide[s.side] = s;
  }

  const feed = [...submissions].sort((a, b) => new Date(a.received_at).getTime() - new Date(b.received_at).getTime());

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
  const winnerName =
    battle.winner === "tie" ? null : battle.winner ? sideName(battle.winner, agentAName, agentBName) : null;
  const eloA = eloDelta(battle.elo_a_before, battle.elo_a_after);
  const eloB = eloDelta(battle.elo_b_before, battle.elo_b_after);

  return (
    <div className="mt-6 space-y-6">
      {err && <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300">{err}</div>}

      {!loaded && (
        <div className="rounded-lg border border-neutral-800 bg-neutral-900/30 p-4 space-y-2" aria-hidden="true">
          <div className="animate-pulse bg-neutral-800/50 rounded-md h-3.5" style={{ width: "100%" }} />
          <div className="animate-pulse bg-neutral-800/50 rounded-md h-3.5" style={{ width: "88%" }} />
          <div className="animate-pulse bg-neutral-800/50 rounded-md h-3.5" style={{ width: "62%" }} />
        </div>
      )}

      {loaded && (
        <>
          {/* Ход боя — checkpoint feed, turns are closed once we get here */}
          {feed.length > 0 && (
            <div>
              <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-neutral-500 mb-3">Ход боя</div>
              <div className="space-y-1.5">
                {feed.map((s, i) => (
                  <div
                    key={`${s.side}-${s.seq_no}-${i}`}
                    className={`rounded-lg border px-3 py-2.5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1.5 ${
                      s.is_final
                        ? s.side === "a"
                          ? "border-violet-500/30 bg-violet-500/[0.04]"
                          : "border-cyan-500/30 bg-cyan-500/[0.04]"
                        : "border-neutral-800/80 bg-neutral-900/25"
                    }`}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${s.side === "a" ? "bg-violet-400" : "bg-cyan-400"}`} />
                      <span className="text-xs font-mono uppercase tracking-wide text-neutral-500 shrink-0">
                        Чекпоинт {s.seq_no}
                      </span>
                      {s.is_final && (
                        <span className="text-sm font-medium text-neutral-100">Финальный ответ отправлен</span>
                      )}
                      {s.error && <span className="text-sm text-rose-400">Ответ завершился с ошибкой</span>}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-neutral-500 shrink-0">
                      {s.tokens_used !== null && <span className="font-mono tabular-nums">{s.tokens_used} ток.</span>}
                      <span className="font-mono tabular-nums">{new Date(s.received_at).toLocaleTimeString("ru-RU")}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {isCompleted && (
            <div className="battle-verdict-enter overflow-hidden rounded-2xl border border-emerald-500/30 bg-neutral-900/45 shadow-[0_16px_48px_rgba(0,0,0,0.28)]">
              {/* 1. Winner announcement */}
              <div className="p-5 sm:p-6">
                <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-emerald-400 mb-2">Вердикт</div>
                {battle.winner && battle.winner !== "tie" ? (
                  <>
                    <div className="text-xs text-neutral-500 mb-1">Победитель</div>
                    <div className={`text-2xl sm:text-[28px] leading-8 font-semibold tracking-[-0.025em] ${battle.winner === "a" ? "text-violet-300" : "text-cyan-300"}`}>
                      {winnerName}
                    </div>
                    <span className="inline-flex items-center mt-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-300">
                      Сторона {battle.winner.toUpperCase()}
                    </span>
                  </>
                ) : battle.winner === "tie" ? (
                  <div className="text-2xl sm:text-[28px] leading-8 font-semibold tracking-[-0.025em] text-neutral-200">Ничья</div>
                ) : (
                  <div>
                    <div className="text-lg font-semibold text-amber-300">Вердикт не вынесен</div>
                    <div className="text-sm text-neutral-400 mt-0.5">Кворум реплик не набран</div>
                  </div>
                )}
                {battle.verdict_reason && (
                  <p className="text-sm text-neutral-300 leading-6 max-w-[70ch] mt-3">{battle.verdict_reason}</p>
                )}
              </div>

              {/* 2. Elo */}
              <div className="grid grid-cols-2 border-t border-neutral-800">
                {(["a", "b"] as const).map((side) => {
                  const before = side === "a" ? battle.elo_a_before : battle.elo_b_before;
                  const after = side === "a" ? battle.elo_a_after : battle.elo_b_after;
                  const delta = side === "a" ? eloA : eloB;
                  return (
                    <div key={side} className={`p-4 sm:p-5 ${side === "a" ? "" : "border-l border-neutral-800"}`}>
                      <div className={`text-xs font-medium truncate ${side === "a" ? "text-violet-300" : "text-cyan-300"}`}>
                        {sideName(side, agentAName, agentBName)}
                      </div>
                      <div className="font-mono tabular-nums text-sm sm:text-base mt-1">
                        {before ?? "—"} → {after ?? "—"} <span className={delta.tone}>({delta.text})</span>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* 3. Both final answers */}
              <div className="p-5 sm:p-6 border-t border-neutral-800">
                <div className="text-sm font-semibold text-neutral-100 mb-3">Финальные ответы</div>
                <div className="grid md:grid-cols-2 gap-4">
                  {(["a", "b"] as const).map((side) => {
                    const sub = finalBySide[side];
                    const isWinner = battle.winner === side;
                    return (
                      <div
                        key={side}
                        className={`rounded-lg border p-4 ${side === "a" ? "border-violet-500/30" : "border-cyan-500/30"}`}
                      >
                        <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
                          <AgentIdentity side={side} agentId={side === "a" ? battle.agent_a_id : battle.agent_b_id} name={sideName(side, agentAName, agentBName)} size="sm" />
                          <div className="flex items-center gap-1.5">
                            <span className="text-[10px] font-mono uppercase tracking-wide text-neutral-600 border border-neutral-700 rounded px-1.5 py-0.5">
                              Финальный ответ
                            </span>
                            {isWinner && (
                              <span className="text-[10px] font-mono uppercase tracking-wide text-emerald-300 border border-emerald-500/30 bg-emerald-500/10 rounded px-1.5 py-0.5">
                                Победитель
                              </span>
                            )}
                          </div>
                        </div>
                        {!sub ? (
                          <div className="text-sm text-neutral-500">Финальный ответ не поступил</div>
                        ) : sub.content_withheld ? (
                          <div className="text-sm text-neutral-500 italic">Содержимое скрыто до конца боя</div>
                        ) : sub.error ? (
                          <div className="rounded-md border border-neutral-800 bg-neutral-950/40 px-3 py-2 text-sm text-neutral-400 flex items-center gap-2">
                            <span className="text-[10px] font-mono uppercase tracking-wide text-rose-400 border border-neutral-700 rounded px-1.5 py-0.5 shrink-0">
                              Ошибка генерации
                            </span>
                          </div>
                        ) : (
                          <>
                            <div className="text-sm leading-6 text-neutral-200 whitespace-pre-wrap max-h-[520px] overflow-y-auto">
                              {sub.content}
                            </div>
                            {sub.truncated && (
                              <div className="text-xs text-amber-400 mt-2">Ответ обрезан по лимиту</div>
                            )}
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* 4. Реплики модели */}
              {llmJudgements.length > 0 && (
                <div className="p-5 sm:p-6 border-t border-neutral-800">
                  <div className="text-sm font-semibold text-neutral-100">Реплики модели</div>
                  <p className="text-xs text-neutral-500 mt-1 mb-4">
                    Три независимых прогона одной модели; порядок A/B проверяется отдельно.
                  </p>
                  <div className="grid md:grid-cols-3 gap-3">
                    {llmJudgements.map((j, i) => (
                      <div key={j.replicate_seed} className="rounded-lg border border-neutral-800 bg-neutral-900/30 p-3">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-medium text-neutral-300">Реплика {i + 1}</span>
                          <VoteChip vote={j.vote} agentAName={agentAName} agentBName={agentBName} />
                        </div>
                        <ConfidenceBar confidence={j.confidence} vote={j.vote} />
                        {j.reasoning && (
                          <div className="mt-3 border-t border-neutral-800 pt-3 text-sm leading-6 text-neutral-300">
                            {j.reasoning}
                          </div>
                        )}
                        {j.position_sensitive && (
                          <span className="inline-flex items-center mt-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-300">
                            Зависит от порядка A/B
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                  {llmTally && <TallyLine tally={llmTally} agentAName={agentAName} agentBName={agentBName} />}
                </div>
              )}

              {/* 5. Человеческие голоса — separate tally, never merged with LLM */}
              {humanJudgements.length > 0 && (
                <div className="p-5 sm:p-6 border-t border-neutral-800">
                  <div className="text-sm font-semibold text-neutral-100 mb-3">Человеческие голоса</div>
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

              {/* 6. Raw runs — collapsible position-bias control, grouped by replicate */}
              {verdict && verdict.runs.length > 0 && (
                <div className="p-5 sm:p-6 border-t border-neutral-800 bg-neutral-950/40">
                  <Disclosure
                    label={`Технические прогоны · ${verdict.runs.length}`}
                    openLabel={`Скрыть технические прогоны · ${verdict.runs.length}`}
                    className="min-h-11 flex items-center"
                  >
                    <div className="space-y-3">
                      {Array.from(runsBySeed.entries()).map(([seed, runs], i) => (
                        <div key={seed} className="rounded-md border border-neutral-800 p-3 text-xs">
                          <div className="text-neutral-400 mb-2">Реплика {i + 1}</div>
                          <div className="space-y-2">
                            {runs.map((run) => (
                              <div key={run.presented_order} className="border-t border-neutral-800/70 pt-2 first:border-t-0 first:pt-0">
                                <div className="flex items-center justify-between gap-2 flex-wrap">
                                  <span className="text-neutral-400">
                                    Порядок {run.presented_order === "ab" ? "A→B" : "B→A"} · статус {run.status}
                                  </span>
                                  <VoteChip vote={run.vote} agentAName={agentAName} agentBName={agentBName} />
                                </div>
                                {run.confidence !== null && (
                                  <div className="text-neutral-500 mt-1">
                                    Уверенность: {Math.round(run.confidence * 100)}%
                                  </div>
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
        </>
      )}
    </div>
  );
}

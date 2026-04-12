"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_URL } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";

type Panelist = {
  id: string;
  display_name: string;
  adapter: string;
  model_id: string | null;
  role: string;
  perspective: string | null;
};

type Council = {
  id: string;
  topic: string;
  brief: string;
  mode: string;
  status: string;
  current_round: number;
  max_rounds: number;
  panel_size: number;
  resolution: string | null;
  consensus_score: number | null;
};

type Message = {
  id: string;
  kind: string;
  round_num: number;
  panelist_id: string;
  content: string;
  created_at: string;
};

type Vote = {
  panelist_id: string;
  vote: string;
  confidence: number;
  reasoning: string | null;
};

function statusBadge(s: string): string {
  switch (s) {
    case "done": return "bg-emerald-500/10 text-emerald-400 border-emerald-500/30";
    case "aborted": return "bg-red-500/10 text-red-400 border-red-500/30";
    case "round":
    case "voting":
    case "synthesizing":
    case "briefing": return "bg-violet-500/10 text-violet-300 border-violet-500/30 animate-pulse";
    default: return "bg-neutral-500/10 text-neutral-400 border-neutral-500/30";
  }
}

function statusLabel(s: string): string {
  switch (s) {
    case "convening": return "assembling panel";
    case "briefing": return "briefing panel";
    case "round": return "debating";
    case "voting": return "voting";
    case "synthesizing": return "writing resolution";
    case "done": return "finished";
    case "aborted": return "aborted";
    default: return s;
  }
}

function roleColor(role: string): string {
  switch (role) {
    case "devil_advocate": return "text-orange-400";
    case "moderator": return "text-violet-400";
    default: return "text-cyan-300";
  }
}

// A message is an upstream failure placeholder when the adapter wrote
// a bracketed error note. These look like "[Foo is rate-limited...]" or
// "[VOTE] ERROR (conf=0.00) — [error: ...]".
function messageError(content: string): { isError: boolean; isVote: boolean; headline: string; detail: string } {
  const isVote = content.startsWith("[VOTE]");
  const errorPatterns = [
    /rate-limited/i,
    /unreachable/i,
    /upstream is flaky/i,
    /refused the request/i,
    /out of free credits/i,
    /no response/i,
    /\[error:/i,
    /\] ERROR /,
  ];
  const isError = errorPatterns.some(r => r.test(content));
  if (!isError) return { isError: false, isVote, headline: "", detail: "" };
  // Strip surrounding brackets for display.
  const trimmed = content.replace(/^\[/, "").replace(/\]$/, "");
  return { isError: true, isVote, headline: isVote ? "Vote unavailable" : "Response unavailable", detail: trimmed };
}

function voteBadge(vote: string): string {
  switch (vote) {
    case "approve": return "bg-emerald-500/10 text-emerald-400 border-emerald-500/30";
    case "reject": return "bg-red-500/10 text-red-400 border-red-500/30";
    case "abstain": return "bg-neutral-500/10 text-neutral-400 border-neutral-500/30";
    case "error": return "bg-amber-500/10 text-amber-400 border-amber-500/30";
    default: return "bg-neutral-500/10 text-neutral-400 border-neutral-500/30";
  }
}

export default function CouncilPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [council, setCouncil] = useState<Council | null>(null);
  const [panelists, setPanelists] = useState<Panelist[]>([]);
  const [votes, setVotes] = useState<Vote[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [aborting, setAborting] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!id) return;
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace(`/login?next=/councils/${id}`);
      return;
    }
    let alive = true;

    const load = async () => {
      try {
        const [cRes, mRes] = await Promise.all([
          fetchWithAuth(`${API_URL}/api/v1/councils/${id}`),
          fetchWithAuth(`${API_URL}/api/v1/councils/${id}/messages`),
        ]);
        if (cRes.status === 401) { router.replace(`/login?next=/councils/${id}`); return; }
        if (cRes.status === 403) { setErr("Not your council"); return; }
        if (!cRes.ok) throw new Error("council not found");
        const cData = await cRes.json();
        const mData = await mRes.json();
        if (!alive) return;
        setCouncil(cData.council);
        setPanelists(cData.panelists);
        setVotes(cData.votes);
        setMessages(mData);
        setErr(null);
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : "failed");
      }
    };
    load();
    const poll = setInterval(load, 2000);
    return () => { alive = false; clearInterval(poll); };
  }, [id, router]);

  const abort = async () => {
    if (!id || !confirm("Abort this council? Running rounds will stop but already-saved messages remain.")) return;
    setAborting(true);
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/councils/${id}/abort`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "abort failed");
    } finally {
      setAborting(false);
    }
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const panelistById = new Map(panelists.map(p => [p.id, p]));
  const discussion = messages.filter(m => m.kind !== "brief" && m.kind !== "resolution");
  const brief = messages.find(m => m.kind === "brief");
  const resolution = messages.find(m => m.kind === "resolution");

  const totalPanelists = panelists.filter(p => p.role !== "moderator").length || panelists.length;
  const errorVotes = votes.filter(v => v.vote === "error").length;
  const allErrored = votes.length > 0 && errorVotes === votes.length;
  const showWorkingNotice = council?.status !== "done" && council?.status !== "aborted" && panelists.length > 0;

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-4xl px-4 py-8">
        {err && <div className="text-red-400 mb-4">{err}</div>}
        {council && (
          <>
            <div className="flex items-start justify-between gap-4 mb-6">
              <div>
                <h1 className="text-2xl font-semibold tracking-tight">{council.topic}</h1>
                <div className="text-sm text-neutral-500 mt-1">
                  {council.mode} · round {council.current_round}/{council.max_rounds} · {council.panel_size} panelists
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className={`text-xs px-2 py-0.5 rounded border ${statusBadge(council.status)}`}>
                  {statusLabel(council.status)}
                </span>
                {!["done", "aborted"].includes(council.status) && (
                  <button
                    onClick={abort}
                    disabled={aborting}
                    className="text-xs px-2 py-0.5 rounded border border-red-500/30 text-red-400 hover:bg-red-500/10 disabled:opacity-50"
                    title="Stop the council immediately"
                  >
                    {aborting ? "Aborting..." : "Abort"}
                  </button>
                )}
              </div>
            </div>

            {showWorkingNotice && (
              <div className="mb-4 rounded-lg border border-violet-500/20 bg-violet-500/5 p-3 text-xs text-violet-200/80">
                The panel is running in the background. This page refreshes automatically every 2 seconds — feel free to leave and come back.
              </div>
            )}
            {allErrored && (
              <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-300">
                <div className="font-medium mb-0.5">All panelists failed to vote.</div>
                The free OpenRouter tier is rate-limiting these models right now. Convene a new council in a few minutes — we retry automatically with exponential backoff, so most transient issues resolve themselves.
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-[1fr_220px] gap-6">
              {/* Main column */}
              <div>
                {/* Brief */}
                {brief && (
                  <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4 mb-4">
                    <div className="text-xs uppercase text-neutral-500 mb-2">Brief</div>
                    <div className="text-sm text-neutral-300 whitespace-pre-wrap">{brief.content}</div>
                  </div>
                )}

                {/* Discussion */}
                <div className="space-y-3">
                  {discussion.length === 0 && council.status !== "done" && council.status !== "aborted" && (
                    <div className="rounded-lg border border-neutral-800 bg-neutral-900/30 p-4 text-sm text-neutral-500">
                      Waiting for the first panelist to respond...
                    </div>
                  )}
                  {discussion.map(m => {
                    const p = panelistById.get(m.panelist_id);
                    const info = messageError(m.content);
                    if (info.isError) {
                      return (
                        <div
                          key={m.id}
                          className="rounded-lg border border-amber-500/25 bg-amber-500/5 p-3"
                        >
                          <div className="flex items-center gap-2 mb-1">
                            <span className={`font-medium ${p ? roleColor(p.role) : "text-neutral-400"}`}>
                              {p?.display_name || "System"}
                            </span>
                            <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/30">
                              {info.isVote ? "vote failed" : "no response"}
                            </span>
                            <span className="text-xs text-neutral-600">round {m.round_num}</span>
                          </div>
                          <div className="text-xs font-medium text-amber-300 mb-1">{info.headline}</div>
                          <div className="text-xs text-amber-200/70 whitespace-pre-wrap leading-relaxed">
                            {info.detail}
                          </div>
                        </div>
                      );
                    }
                    return (
                      <div
                        key={m.id}
                        className={`rounded-lg border p-3 ${
                          info.isVote
                            ? "border-violet-500/30 bg-violet-500/5"
                            : "border-neutral-800 bg-neutral-900/30"
                        }`}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`font-medium ${p ? roleColor(p.role) : "text-neutral-400"}`}>
                            {p?.display_name || "System"}
                          </span>
                          {p?.role === "devil_advocate" && (
                            <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-orange-500/10 text-orange-400 border border-orange-500/30">
                              devil
                            </span>
                          )}
                          <span className="text-xs text-neutral-600">round {m.round_num}</span>
                        </div>
                        <div className="text-sm text-neutral-200 whitespace-pre-wrap">{m.content}</div>
                      </div>
                    );
                  })}
                  <div ref={bottomRef} />
                </div>

                {/* Resolution */}
                {resolution && (
                  <div className="mt-6 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-5">
                    <div className="text-xs uppercase text-emerald-400 mb-2">Resolution</div>
                    <div className="prose prose-invert prose-sm max-w-none">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{resolution.content}</ReactMarkdown>
                    </div>
                  </div>
                )}
              </div>

              {/* Sidebar */}
              <aside className="md:sticky md:top-20 h-fit">
                <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-4">
                  <div className="text-xs uppercase text-neutral-500 mb-2">Panel · {panelists.length}</div>
                  <ul className="space-y-2.5">
                    {panelists.map(p => {
                      const v = votes.find(vv => vv.panelist_id === p.id);
                      return (
                        <li key={p.id} className="text-sm">
                          <div className="flex items-center gap-1.5">
                            <div className={`font-medium truncate ${roleColor(p.role)}`}>{p.display_name}</div>
                            {p.role === "devil_advocate" && (
                              <span className="shrink-0 text-[9px] uppercase px-1 rounded bg-orange-500/10 text-orange-400 border border-orange-500/30">
                                devil
                              </span>
                            )}
                          </div>
                          <div className="text-[10px] font-mono text-neutral-600 truncate" title={p.model_id || p.adapter}>
                            {p.model_id?.replace(":free", "") || p.adapter}
                          </div>
                          {v ? (
                            <div className="flex items-center gap-1.5 mt-1">
                              <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded border ${voteBadge(v.vote)}`}>
                                {v.vote}
                              </span>
                              {v.vote !== "error" && (
                                <span className="text-[10px] text-neutral-600 font-mono">
                                  {v.confidence.toFixed(2)}
                                </span>
                              )}
                            </div>
                          ) : (
                            council.status !== "done" && council.status !== "aborted" && (
                              <div className="text-[10px] text-neutral-600 mt-1">waiting...</div>
                            )
                          )}
                        </li>
                      );
                    })}
                  </ul>

                  {council.consensus_score !== null && (
                    <div className="mt-4 pt-3 border-t border-neutral-800">
                      <div className="text-xs uppercase text-neutral-500 mb-1">Consensus</div>
                      <div className="text-lg font-mono">
                        {council.consensus_score > 0 ? "+" : ""}
                        {council.consensus_score.toFixed(2)}
                      </div>
                      <div className="text-[10px] text-neutral-600 mt-0.5">
                        {council.consensus_score > 0.5 ? "strong approve"
                          : council.consensus_score > 0 ? "lean approve"
                          : council.consensus_score === 0 ? "split"
                          : council.consensus_score > -0.5 ? "lean reject"
                          : "strong reject"}
                      </div>
                    </div>
                  )}
                </div>
              </aside>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

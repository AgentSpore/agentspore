"use client";

import Link from "next/link";
import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { API_URL, Flow, FlowStep, FLOW_STATUS, STEP_STATUS, timeAgo } from "@/lib/api";
import { Header } from "@/components/Header";

function DotGrid() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0" style={{
        backgroundImage: "radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }} />
      <div className="absolute top-20 -left-32 w-[500px] h-[500px] rounded-full opacity-[0.07]"
        style={{ background: "radial-gradient(circle, rgb(139 92 246), transparent 70%)" }} />
      <div className="absolute bottom-20 -right-32 w-[400px] h-[400px] rounded-full opacity-[0.05]"
        style={{ background: "radial-gradient(circle, rgb(34 211 238), transparent 70%)" }} />
    </div>
  );
}

const stepStatusDot: Record<string, string> = {
  pending: "bg-neutral-600",
  ready: "bg-cyan-400",
  active: "bg-violet-400 animate-pulse",
  review: "bg-orange-400 animate-pulse",
  approved: "bg-emerald-400",
  failed: "bg-red-400",
  skipped: "bg-neutral-500",
};

export default function FlowDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [flow, setFlow] = useState<Flow | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [approveStepId, setApproveStepId] = useState<string | null>(null);
  const [editedOutput, setEditedOutput] = useState("");
  const [rejectStepId, setRejectStepId] = useState<string | null>(null);
  const [rejectFeedback, setRejectFeedback] = useState("");

  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;

  const loadFlow = useCallback(() => {
    if (!token || !id) return;
    fetch(`${API_URL}/api/v1/flows/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setFlow(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [id, token]);

  useEffect(() => {
    loadFlow();
    const interval = setInterval(loadFlow, 5000);
    return () => clearInterval(interval);
  }, [loadFlow]);

  const flowAction = async (action: string) => {
    if (!token) return;
    setActionLoading(action);
    try {
      const res = await fetch(`${API_URL}/api/v1/flows/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      });
      if (res.ok) loadFlow();
    } finally {
      setActionLoading("");
    }
  };

  const approveStep = async (stepId: string) => {
    if (!token) return;
    setActionLoading(`approve-${stepId}`);
    try {
      const body: Record<string, string> = {};
      if (editedOutput.trim()) body.edited_output = editedOutput.trim();
      await fetch(`${API_URL}/api/v1/flows/${id}/steps/${stepId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(body),
      });
      setApproveStepId(null);
      setEditedOutput("");
      loadFlow();
    } finally {
      setActionLoading("");
    }
  };

  const rejectStep = async (stepId: string) => {
    if (!token || !rejectFeedback.trim()) return;
    setActionLoading(`reject-${stepId}`);
    try {
      await fetch(`${API_URL}/api/v1/flows/${id}/steps/${stepId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ feedback: rejectFeedback.trim() }),
      });
      setRejectStepId(null);
      setRejectFeedback("");
      loadFlow();
    } finally {
      setActionLoading("");
    }
  };

  const skipStep = async (stepId: string) => {
    if (!token) return;
    setActionLoading(`skip-${stepId}`);
    try {
      await fetch(`${API_URL}/api/v1/flows/${id}/steps/${stepId}/skip`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({}),
      });
      loadFlow();
    } finally {
      setActionLoading("");
    }
  };

  const steps = flow?.steps || [];
  const st = flow ? FLOW_STATUS[flow.status] || FLOW_STATUS.draft : FLOW_STATUS.draft;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />

      <main className="relative z-10 max-w-3xl mx-auto px-6 py-10 space-y-8">
        {loading && <p className="text-neutral-600 text-sm font-mono fade-up">Loading...</p>}

        {!loading && !flow && (
          <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-12 text-center space-y-3 fade-up">
            <p className="text-neutral-500">Flow not found</p>
            <Link href="/flows" className="text-sm text-neutral-400 hover:text-white mt-2 inline-block font-mono">
              Back to Flows
            </Link>
          </div>
        )}

        {flow && (
          <>
            {/* Breadcrumb + Header */}
            <div className="fade-up" style={{ animationDelay: "0ms" }}>
              <div className="flex items-center gap-1.5 mb-3">
                <Link href="/flows" className="text-[10px] font-mono text-neutral-600 hover:text-neutral-400 transition-colors">
                  Flows
                </Link>
                <span className="text-[10px] text-neutral-700">/</span>
                <span className="text-[10px] font-mono text-neutral-500 truncate max-w-[200px]">
                  {flow.title}
                </span>
              </div>

              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h1 className="text-2xl font-bold truncate tracking-tight">{flow.title}</h1>
                  {flow.description && (
                    <p className="text-neutral-500 text-sm mt-1">{flow.description}</p>
                  )}
                  <div className="flex items-center gap-3 mt-2 text-[11px] text-neutral-600 font-mono">
                    <span>{timeAgo(flow.created_at)}</span>
                    <span className="text-neutral-800">|</span>
                    <span>{steps.length} steps</span>
                  </div>
                </div>
                <span className={`text-[10px] px-2.5 py-1 rounded-full border font-mono flex-shrink-0 ${st.classes}`}>
                  {st.label}
                </span>
              </div>
            </div>

            {/* Flow controls */}
            <div className="flex items-center gap-2 flex-wrap fade-up" style={{ animationDelay: "60ms" }}>
              {flow.status === "draft" && (
                <button
                  onClick={() => flowAction("start")}
                  disabled={!!actionLoading || steps.length === 0}
                  className="px-5 py-2.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50"
                >
                  {actionLoading === "start" ? "Starting..." : "Start Flow"}
                </button>
              )}
              {flow.status === "running" && (
                <button
                  onClick={() => flowAction("pause")}
                  disabled={!!actionLoading}
                  className="px-5 py-2.5 rounded-lg text-sm font-mono bg-neutral-800/30 border border-neutral-800/50 text-neutral-300 hover:text-white hover:border-neutral-700/60 transition-all"
                >
                  Pause
                </button>
              )}
              {flow.status === "paused" && (
                <button
                  onClick={() => flowAction("resume")}
                  disabled={!!actionLoading}
                  className="px-5 py-2.5 rounded-lg text-sm font-mono bg-white text-black hover:bg-neutral-200 transition-all"
                >
                  Resume
                </button>
              )}
              {["draft", "running", "paused"].includes(flow.status) && (
                <button
                  onClick={() => flowAction("cancel")}
                  disabled={!!actionLoading}
                  className="px-5 py-2.5 rounded-lg text-sm font-mono bg-neutral-800/30 border border-red-500/20 text-red-400 hover:text-red-300 hover:border-red-500/40 transition-all"
                >
                  Cancel
                </button>
              )}
              {flow.status === "draft" && (
                <Link
                  href={`/flows/new`}
                  className="px-5 py-2.5 rounded-lg text-sm font-mono bg-neutral-800/30 border border-neutral-800/50 text-neutral-400 hover:text-white hover:border-neutral-700/60 transition-all ml-auto"
                >
                  Edit Steps
                </Link>
              )}
            </div>

            {/* Pipeline visualization */}
            <div className="space-y-1 fade-up" style={{ animationDelay: "120ms" }}>
              <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600">
                Pipeline
              </span>

              {steps.length === 0 && (
                <div className="rounded-xl border border-neutral-800/50 bg-neutral-900/30 backdrop-blur-sm p-8 text-center text-neutral-600 text-sm font-mono mt-2">
                  No steps added yet
                </div>
              )}

              <div className="mt-3 space-y-0">
                {steps.map((step: FlowStep, idx: number) => {
                  const sts = STEP_STATUS[step.status] || STEP_STATUS.pending;
                  const isReview = step.status === "review";
                  const deps = step.depends_on
                    .map((depId) => steps.find((s) => s.id === depId))
                    .filter(Boolean) as FlowStep[];
                  const dotColor = stepStatusDot[step.status] || stepStatusDot.pending;
                  const isLast = idx === steps.length - 1;

                  return (
                    <div key={step.id} className="flex gap-4 fade-up" style={{ animationDelay: `${160 + idx * 50}ms` }}>
                      {/* Timeline connector */}
                      <div className="flex flex-col items-center flex-shrink-0 pt-5">
                        <div className={`w-2.5 h-2.5 rounded-full ${dotColor} ring-2 ring-[#0a0a0a]`} />
                        {!isLast && (
                          <div className="w-px flex-1 bg-neutral-800/60 mt-0.5" />
                        )}
                      </div>

                      {/* Step card */}
                      <div
                        className={`flex-1 rounded-xl border bg-neutral-900/30 backdrop-blur-sm p-4 space-y-3 transition-all mb-2 ${
                          isReview ? "border-orange-500/30 shadow-[0_0_20px_rgba(251,146,60,0.05)]" : "border-neutral-800/50 hover:border-neutral-700/60"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-[10px] font-mono text-neutral-600">{String(idx + 1).padStart(2, "0")}</span>
                              <span className="text-white font-medium text-sm truncate">{step.title}</span>
                            </div>
                            <div className="flex items-center gap-2 mt-1.5 text-[11px] text-neutral-500 font-mono">
                              <span className="text-violet-400/70">@{step.agent_handle || "unknown"}</span>
                              {step.auto_approve && (
                                <>
                                  <span className="text-neutral-800">|</span>
                                  <span className="text-cyan-400/50">auto-approve</span>
                                </>
                              )}
                              {deps.length > 0 && (
                                <>
                                  <span className="text-neutral-800">|</span>
                                  <span className="text-neutral-600">depends: {deps.map((d) => d.title).join(", ")}</span>
                                </>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-2 flex-shrink-0">
                            <span className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${sts.classes}`}>
                              {sts.label}
                            </span>
                            <Link
                              href={`/flows/${id}/steps/${step.id}`}
                              className="text-[10px] font-mono text-neutral-600 hover:text-violet-400 transition-colors px-2 py-1 rounded-md border border-neutral-800/50 hover:border-violet-500/30"
                            >
                              Chat
                            </Link>
                          </div>
                        </div>

                        {/* Output preview for review */}
                        {isReview && step.output_text && (
                          <div className="rounded-lg bg-neutral-900/50 border border-neutral-800/50 p-3">
                            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 block mb-1.5">Agent output</span>
                            <pre className="text-xs text-neutral-300 whitespace-pre-wrap font-mono max-h-40 overflow-y-auto leading-relaxed">
                              {step.output_text}
                            </pre>
                          </div>
                        )}

                        {/* Approve inline */}
                        {isReview && approveStepId === step.id && (
                          <div className="space-y-2">
                            <textarea
                              value={editedOutput}
                              onChange={(e) => setEditedOutput(e.target.value)}
                              placeholder="Edit output before approving (optional)"
                              rows={3}
                              className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-3 py-2 text-xs text-white placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors resize-none font-mono"
                            />
                            <div className="flex gap-2">
                              <button
                                onClick={() => approveStep(step.id)}
                                disabled={!!actionLoading}
                                className="px-4 py-1.5 rounded-lg text-xs font-mono bg-white text-black hover:bg-neutral-200 transition-all"
                              >
                                Confirm Approve
                              </button>
                              <button
                                onClick={() => { setApproveStepId(null); setEditedOutput(""); }}
                                className="px-4 py-1.5 rounded-lg text-xs font-mono text-neutral-500 hover:text-neutral-300 transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}

                        {/* Reject inline */}
                        {isReview && rejectStepId === step.id && (
                          <div className="space-y-2">
                            <textarea
                              value={rejectFeedback}
                              onChange={(e) => setRejectFeedback(e.target.value)}
                              placeholder="Feedback for the agent (what to fix)"
                              rows={2}
                              className="w-full bg-neutral-900/50 border border-neutral-800/50 rounded-lg px-3 py-2 text-xs text-white placeholder-neutral-600 focus:outline-none focus:border-violet-500/50 transition-colors resize-none font-mono"
                            />
                            <div className="flex gap-2">
                              <button
                                onClick={() => rejectStep(step.id)}
                                disabled={!!actionLoading || !rejectFeedback.trim()}
                                className="px-4 py-1.5 rounded-lg text-xs font-mono bg-neutral-800/30 border border-red-500/20 text-red-400 hover:text-red-300 transition-all disabled:opacity-50"
                              >
                                Confirm Reject
                              </button>
                              <button
                                onClick={() => { setRejectStepId(null); setRejectFeedback(""); }}
                                className="px-4 py-1.5 rounded-lg text-xs font-mono text-neutral-500 hover:text-neutral-300 transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        )}

                        {/* Action buttons */}
                        {isReview && !approveStepId && !rejectStepId && (
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => { setApproveStepId(step.id); setEditedOutput(step.output_text || ""); }}
                              className="px-4 py-1.5 rounded-lg text-xs font-mono bg-white text-black hover:bg-neutral-200 transition-all"
                            >
                              Approve
                            </button>
                            <button
                              onClick={() => setRejectStepId(step.id)}
                              className="px-4 py-1.5 rounded-lg text-xs font-mono bg-neutral-800/30 border border-red-500/20 text-red-400 hover:text-red-300 transition-all"
                            >
                              Reject
                            </button>
                            <button
                              onClick={() => skipStep(step.id)}
                              disabled={!!actionLoading}
                              className="px-4 py-1.5 rounded-lg text-xs font-mono text-neutral-500 hover:text-neutral-300 transition-colors"
                            >
                              Skip
                            </button>
                          </div>
                        )}

                        {/* Skip for pending/ready/active */}
                        {["pending", "ready", "active"].includes(step.status) && flow.status !== "draft" && (
                          <button
                            onClick={() => skipStep(step.id)}
                            disabled={!!actionLoading}
                            className="text-[11px] font-mono text-neutral-600 hover:text-neutral-400 transition-colors"
                          >
                            Skip this step
                          </button>
                        )}

                        {/* Approved/skipped output */}
                        {step.status === "approved" && step.output_text && (
                          <div className="rounded-lg bg-neutral-900/50 border border-emerald-500/10 p-3">
                            <span className="text-[10px] font-mono uppercase tracking-[0.2em] text-emerald-500/50 block mb-1.5">Approved output</span>
                            <pre className="text-xs text-neutral-400 whitespace-pre-wrap font-mono max-h-32 overflow-y-auto leading-relaxed">
                              {step.output_text}
                            </pre>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </main>

      <style jsx global>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .fade-up {
          opacity: 0;
          animation: fadeUp 0.5s ease-out forwards;
        }
      `}</style>
    </div>
  );
}

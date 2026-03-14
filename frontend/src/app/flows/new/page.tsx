"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API_URL, Agent } from "@/lib/api";
import { Header } from "@/components/Header";

interface StepDraft {
  key: string;
  agent_id: string;
  title: string;
  instructions: string;
  depends_on: string[];
  auto_approve: boolean;
}

let keyCounter = 0;
function nextKey() { return `step-${++keyCounter}`; }

export default function NewFlowPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState<StepDraft[]>([
    { key: nextKey(), agent_id: "", title: "", instructions: "", depends_on: [], auto_approve: false },
  ]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API_URL}/api/v1/agents/leaderboard`)
      .then((r) => r.json())
      .then((d) => setAgents(Array.isArray(d) ? d : []))
      .catch(() => {});
  }, []);

  const addStep = () => {
    setSteps((prev) => [
      ...prev,
      { key: nextKey(), agent_id: "", title: "", instructions: "", depends_on: [], auto_approve: false },
    ]);
  };

  const removeStep = (key: string) => {
    setSteps((prev) => {
      const filtered = prev.filter((s) => s.key !== key);
      return filtered.map((s) => ({
        ...s,
        depends_on: s.depends_on.filter((d) => filtered.some((f) => f.key === d)),
      }));
    });
  };

  const updateStep = (key: string, field: string, value: unknown) => {
    setSteps((prev) =>
      prev.map((s) => (s.key === key ? { ...s, [field]: value } : s)),
    );
  };

  const toggleDep = (stepKey: string, depKey: string) => {
    setSteps((prev) =>
      prev.map((s) => {
        if (s.key !== stepKey) return s;
        const deps = s.depends_on.includes(depKey)
          ? s.depends_on.filter((d) => d !== depKey)
          : [...s.depends_on, depKey];
        return { ...s, depends_on: deps };
      }),
    );
  };

  const handleSubmit = async () => {
    setError("");
    const token = localStorage.getItem("access_token");
    if (!token) { setError("Please sign in first"); return; }
    if (!title.trim()) { setError("Flow title is required"); return; }
    if (steps.some((s) => !s.title.trim() || !s.agent_id)) {
      setError("Every step needs a title and an agent");
      return;
    }

    setSubmitting(true);
    try {
      // 1. Create flow
      const flowRes = await fetch(`${API_URL}/api/v1/flows`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ title: title.trim(), description: description.trim() || null }),
      });
      if (!flowRes.ok) throw new Error((await flowRes.json()).detail || "Failed to create flow");
      const flow = await flowRes.json();
      const flowId = flow.id;

      // 2. Add steps — sequential to get IDs, then update depends_on
      const stepIdMap: Record<string, string> = {};
      for (const s of steps) {
        const stepRes = await fetch(`${API_URL}/api/v1/flows/${flowId}/steps`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({
            agent_id: s.agent_id,
            title: s.title.trim(),
            instructions: s.instructions.trim() || null,
            depends_on: s.depends_on.map((dk) => stepIdMap[dk]).filter(Boolean),
            auto_approve: s.auto_approve,
          }),
        });
        if (!stepRes.ok) throw new Error((await stepRes.json()).detail || "Failed to add step");
        const stepData = await stepRes.json();
        stepIdMap[s.key] = stepData.id;
      }

      router.push(`/flows/${flowId}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Header />

      <main className="max-w-2xl mx-auto px-6 py-10 space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold">New Flow</h1>
          <Link href="/flows" className="text-xs text-neutral-500 hover:text-neutral-300 transition-colors">
            ← Back to Flows
          </Link>
        </div>

        {/* Flow info */}
        <div className="space-y-3">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Flow title"
            className="w-full bg-neutral-900/50 border border-neutral-800 rounded-xl px-4 py-3 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors"
            maxLength={300}
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description (optional)"
            rows={2}
            className="w-full bg-neutral-900/50 border border-neutral-800 rounded-xl px-4 py-3 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors resize-none"
            maxLength={5000}
          />
        </div>

        {/* Steps */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Steps</h2>
            <span className="text-xs text-neutral-600 font-mono">{steps.length} step{steps.length !== 1 ? "s" : ""}</span>
          </div>

          {steps.map((s, idx) => (
            <div
              key={s.key}
              className="rounded-xl border border-neutral-800/80 bg-neutral-900/50 p-4 space-y-3"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono text-neutral-600">Step {idx + 1}</span>
                {steps.length > 1 && (
                  <button
                    onClick={() => removeStep(s.key)}
                    className="text-xs text-neutral-600 hover:text-red-400 transition-colors"
                  >
                    Remove
                  </button>
                )}
              </div>

              <input
                value={s.title}
                onChange={(e) => updateStep(s.key, "title", e.target.value)}
                placeholder="Step title"
                className="w-full bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors"
                maxLength={300}
              />

              <select
                value={s.agent_id}
                onChange={(e) => updateStep(s.key, "agent_id", e.target.value)}
                className="w-full bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-neutral-600 transition-colors"
              >
                <option value="">Select agent...</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    @{a.handle} — {a.name} ({a.specialization})
                  </option>
                ))}
              </select>

              <textarea
                value={s.instructions}
                onChange={(e) => updateStep(s.key, "instructions", e.target.value)}
                placeholder="Instructions for this step (optional)"
                rows={2}
                className="w-full bg-neutral-800/50 border border-neutral-700/50 rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-neutral-600 transition-colors resize-none"
                maxLength={10000}
              />

              {/* Dependencies */}
              {idx > 0 && (
                <div>
                  <span className="text-xs text-neutral-500 block mb-1.5">Depends on:</span>
                  <div className="flex flex-wrap gap-1.5">
                    {steps.slice(0, idx).map((dep, di) => (
                      <button
                        key={dep.key}
                        onClick={() => toggleDep(s.key, dep.key)}
                        className={`text-xs px-2.5 py-1 rounded-lg border font-mono transition-all ${
                          s.depends_on.includes(dep.key)
                            ? "border-violet-500/40 text-violet-300 bg-violet-500/10"
                            : "border-neutral-700/50 text-neutral-500 hover:text-neutral-300"
                        }`}
                      >
                        Step {di + 1}: {dep.title || "Untitled"}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={s.auto_approve}
                  onChange={(e) => updateStep(s.key, "auto_approve", e.target.checked)}
                  className="rounded border-neutral-600 bg-neutral-800 text-violet-500 focus:ring-0"
                />
                <span className="text-xs text-neutral-500">Auto-approve (skip manual review)</span>
              </label>
            </div>
          ))}

          <button
            onClick={addStep}
            className="w-full py-2.5 rounded-xl border border-dashed border-neutral-700 text-sm text-neutral-500 hover:text-neutral-300 hover:border-neutral-600 transition-all"
          >
            + Add Step
          </button>
        </div>

        {/* DAG preview */}
        {steps.length > 1 && (
          <div className="rounded-xl border border-neutral-800/60 bg-neutral-900/50 p-4">
            <span className="text-xs text-neutral-500 font-mono block mb-2">DAG Preview</span>
            <div className="space-y-1">
              {steps.map((s, idx) => {
                const deps = s.depends_on
                  .map((dk) => steps.findIndex((st) => st.key === dk) + 1)
                  .filter((n) => n > 0);
                return (
                  <div key={s.key} className="text-xs font-mono text-neutral-400">
                    <span className="text-neutral-600">{idx + 1}.</span>{" "}
                    {s.title || "Untitled"}
                    {deps.length > 0 && (
                      <span className="text-neutral-600 ml-2">← {deps.map((n) => `Step ${n}`).join(", ")}</span>
                    )}
                    {deps.length === 0 && idx > 0 && (
                      <span className="text-emerald-500/60 ml-2">∥ parallel</span>
                    )}
                    {deps.length === 0 && idx === 0 && (
                      <span className="text-blue-400/60 ml-2">▸ root</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-xl border border-red-500/20 bg-red-500/[0.05] p-3 text-sm text-red-400">
            {error}
          </div>
        )}

        <button
          onClick={handleSubmit}
          disabled={submitting}
          className="w-full py-3 rounded-xl text-sm font-medium bg-white text-black hover:bg-neutral-200 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {submitting ? "Creating..." : "Create Flow →"}
        </button>
      </main>
    </div>
  );
}

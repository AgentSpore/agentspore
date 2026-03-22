"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API_URL } from "@/lib/api";
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

interface FreeModel {
  id: string;
  name: string;
}

const SPECIALIZATIONS = [
  "programmer", "devops", "researcher", "analyst", "designer", "writer", "tester", "security",
];

export default function CreateHostedAgentPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [specialization, setSpecialization] = useState("programmer");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<FreeModel[]>([]);
  const [skillInput, setSkillInput] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API_URL}/api/v1/hosted-agents/models`)
      .then(r => r.json())
      .then(d => {
        const list: FreeModel[] = d.models || [];
        setModels(list);
        if (list.length > 0 && !model) setModel(list[0].id);
      })
      .catch(() => {});
  }, []);

  const addSkill = () => {
    const s = skillInput.trim();
    if (s && !skills.includes(s)) {
      setSkills(prev => [...prev, s]);
      setSkillInput("");
    }
  };

  const removeSkill = (s: string) => setSkills(prev => prev.filter(x => x !== s));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    const token = localStorage.getItem("access_token");
    if (!token) { setError("Please sign in first"); return; }
    if (!name.trim()) { setError("Agent name is required"); return; }
    if (!systemPrompt.trim() || systemPrompt.trim().length < 10) { setError("System prompt must be at least 10 characters"); return; }

    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/hosted-agents`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim(),
          specialization,
          system_prompt: systemPrompt.trim(),
          model,
          skills,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        if (res.status === 409) {
          setError(data.detail || "You already have a hosted agent. Delete it first to create a new one.");
        } else if (res.status === 502) {
          setError("Service temporarily unavailable. Please try again in a minute.");
        } else {
          setError(data.detail || `Error ${res.status}`);
        }
        return;
      }
      const created = await res.json();
      router.push(`/hosted-agents/${created.id}`);
    } catch {
      setError("Network error");
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls = "w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3.5 py-2.5 text-sm font-mono text-white placeholder:text-neutral-600 focus:outline-none focus:border-violet-500/30 focus:ring-1 focus:ring-violet-500/10 transition-colors";
  const labelCls = "block text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-500 mb-2";

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />
      <div className="relative z-10 max-w-2xl mx-auto px-4 pt-28 pb-20">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-[10px] font-mono text-neutral-600 mb-8">
          <Link href="/hosted-agents" className="hover:text-violet-400 transition-colors">My Agents</Link>
          <span>/</span>
          <span className="text-neutral-500">New</span>
        </div>

        <div className="mb-8">
          <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">Create</p>
          <h1 className="text-2xl font-medium font-mono text-white tracking-tight">New Hosted Agent</h1>
          <p className="text-xs text-neutral-500 mt-1 font-mono">Your agent runs in an isolated environment with file access, memory, and tools — no setup needed</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Name + Specialization */}
          <div className="grid grid-cols-3 gap-4">
            <div className="col-span-2">
              <label className={labelCls}>Agent Name</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)}
                placeholder="MyAssistant" className={inputCls} maxLength={200} />
            </div>
            <div>
              <label className={labelCls}>Role</label>
              <select value={specialization} onChange={e => setSpecialization(e.target.value)}
                className={inputCls + " cursor-pointer"}>
                {SPECIALIZATIONS.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          </div>

          {/* Description */}
          <div>
            <label className={labelCls}>Description</label>
            <input type="text" value={description} onChange={e => setDescription(e.target.value)}
              placeholder="What does this agent do?" className={inputCls} maxLength={500} />
          </div>

          {/* System Prompt */}
          <div>
            <label className={labelCls}>System Prompt <span className="text-violet-400/60">*</span></label>
            <textarea value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)}
              placeholder={"Example: You are a Python developer assistant. You help users write clean, tested code. You can create files, run scripts, and install packages in your sandbox environment."}
              className={inputCls + " min-h-[120px] resize-y"} maxLength={10000} />
            <p className="text-[10px] font-mono text-neutral-700 mt-1">
              Describe your agent's personality and capabilities. This is the main instruction it follows.
            </p>
          </div>

          {/* Model selection */}
          <div>
            <label className={labelCls}>AI Model</label>
            {models.length === 0 ? (
              <p className="text-neutral-600 text-xs font-mono py-2">Loading models…</p>
            ) : (
              <select value={model} onChange={e => setModel(e.target.value)}
                className={inputCls + " cursor-pointer"}>
                {models.map(m => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            )}
            <p className="text-[10px] font-mono text-neutral-700 mt-1">
              All models are free and support tool use. Sorted by context window size.
            </p>
          </div>

          {/* Skills */}
          <div>
            <label className={labelCls}>Skills <span className="text-neutral-700 normal-case tracking-normal">(optional)</span></label>
            <div className="flex gap-2">
              <input type="text" value={skillInput}
                onChange={e => setSkillInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addSkill(); } }}
                placeholder="e.g. code-review, data-analysis, web-scraping" className={inputCls + " flex-1"} />
              <button type="button" onClick={addSkill}
                className="px-3 py-2 text-xs font-mono bg-white/[0.05] border border-neutral-800/50 rounded-lg text-neutral-400 hover:text-white hover:border-neutral-700/50 transition-colors">
                Add
              </button>
            </div>
            <p className="text-[10px] font-mono text-neutral-700 mt-1">
              Tag your agent's capabilities. Other agents can discover it by skills.
            </p>
            {skills.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {skills.map(s => (
                  <span key={s} className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-mono bg-violet-500/[0.08] text-violet-300 border border-violet-500/15 rounded-md">
                    {s}
                    <button type="button" onClick={() => removeSkill(s)} className="text-violet-500/50 hover:text-violet-300 ml-0.5">×</button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="px-4 py-3 text-xs font-mono text-red-400/90 bg-red-400/[0.06] border border-red-400/15 rounded-lg">
              {error}
            </div>
          )}

          {/* Submit */}
          <div className="flex items-center justify-between pt-2">
            <Link href="/hosted-agents" className="text-xs font-mono text-neutral-600 hover:text-neutral-400 transition-colors">
              ← Back
            </Link>
            <button type="submit" disabled={submitting}
              className="px-6 py-2.5 text-sm font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25 rounded-lg hover:bg-violet-500/25 disabled:opacity-40 disabled:cursor-not-allowed transition-all">
              {submitting ? "Creating…" : "Create Agent"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

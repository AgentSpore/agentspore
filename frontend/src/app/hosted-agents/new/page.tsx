"use client";

import { useTranslations } from "@/lib/i18n/LocaleProvider";
import { automationMessages } from "@/lib/i18n/automation";

import { useModelCatalog } from "./useModelCatalog";
import { ModelSelect } from "./ModelSelect";
import { TEMPLATES, type Template } from "./templates";
import { agentOnboardingMessages } from "@/lib/i18n/agentOnboarding";

import { useEffect, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API_URL } from "@/lib/api";
import { Header } from "@/components/Header";

const SPECIALIZATIONS = [
  "programmer", "devops", "researcher", "analyst", "designer", "writer", "tester", "security",
];

export default function CreateHostedAgentPage() {
  const tr = useTranslations(automationMessages);
  const on = useTranslations(agentOnboardingMessages);
  const display = (value: string) => Object.hasOwn(automationMessages.en, value) ? tr(value) : value;

  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [specialization, setSpecialization] = useState("programmer");
  const [systemPrompt, setSystemPrompt] = useState("");

  const [skillInput, setSkillInput] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [activeTemplate, setActiveTemplate] = useState<string | null>(null);
  const authChecked = useSyncExternalStore(() => () => {}, () => Boolean(localStorage.getItem("access_token")), () => false);

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    if (!token) {
      const next = encodeURIComponent("/hosted-agents/new");
      router.replace(`/login?next=${next}`);
      return;
    }
  }, [router]);

  const catalog = useModelCatalog(authChecked);
  const nameLength = Array.from(name.trim()).length;
  const taskLength = Array.from(systemPrompt.trim()).length;

  const applyTemplate = (t: Template) => {
    setActiveTemplate(t.id);
    setName(t.name);
    setDescription(t.description);
    setSpecialization(t.specialization);
    setSystemPrompt(t.systemPrompt);
    setSkills(t.skills);
    setError("");
    if (typeof window !== "undefined") {
      setTimeout(() => {
        document.getElementById("agent-form")?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 50);
    }
  };

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
    if (nameLength < 3 || nameLength > 200) { setError(on("nameError")); return; }
    if (taskLength < 10 || taskLength > 10000) { setError(on("taskError")); return; }
    if (catalog.state.status !== "ready" || !catalog.state.models.some(item => item.id === catalog.model)) { setError(on("modelError")); return; }

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
          model: catalog.model,
          skills,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        if (res.status === 409) {
          setError((typeof data.detail === "string" && data.detail) || "You already have a hosted agent. Delete it first to create a new one.");
        } else if (res.status === 502) {
          setError("Service temporarily unavailable. Please try again in a minute.");
        } else {
          setError((typeof data.detail === "string" && data.detail) || `Error ${res.status}`);
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

  const inputCls = "w-full bg-white/[0.03] border border-neutral-800/50 rounded-lg px-3.5 py-2.5 text-sm text-white placeholder:text-neutral-400 focus:outline-none focus:border-violet-500/30 focus:ring-1 focus:ring-violet-500/10 transition-colors";
  const labelCls = "block text-sm font-medium text-neutral-200 mb-2";

  if (!authChecked) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white relative">
        <Header />
        <div className="relative z-10 flex items-center justify-center pt-40">
          <div className="inline-block w-5 h-5 border border-violet-400/40 border-t-violet-400 rounded-full animate-spin" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <Header />
      <div className="relative z-10 max-w-3xl mx-auto px-4 pt-28 pb-20">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-[10px] font-mono text-neutral-600 mb-8">
          <Link href="/hosted-agents" className="hover:text-violet-400 transition-colors">{tr("My Agents")}</Link>
          <span>/</span>
          <span className="text-neutral-500">{tr("New")}</span>
        </div>

        <div className="mb-8">
          <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">{tr("Create")}</p>
          <h1 className="text-2xl font-medium font-mono text-white tracking-tight">{tr("New Hosted Agent")}</h1>
          <p className="text-sm text-neutral-300 mt-2">{on("intro")}</p>
        </div>

        {/* Templates */}
        <div className="mb-10">
          <div className="flex items-center justify-between mb-4">
            <p className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-600">{tr("Start from template")}</p>
            {activeTemplate && (
              <button
                type="button"
                onClick={() => {
                  setActiveTemplate(null);
                  setName(""); setDescription(""); setSystemPrompt("");
                  setSpecialization("programmer"); setSkills([]);
                }}
                className="text-[10px] font-mono text-neutral-600 hover:text-violet-400 transition-colors"
              >
                {" " + tr("clear ×") + " "}</button>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {TEMPLATES.map(t => {
              const isActive = activeTemplate === t.id;
              return (
                <button
                  aria-pressed={isActive}
                  key={t.id}
                  type="button"
                  onClick={() => applyTemplate(t)}
                  className={`group text-left p-3.5 rounded-xl border transition-all ${
                    isActive
                      ? "bg-violet-500/[0.08] border-violet-500/40"
                      : "bg-white/[0.02] border-neutral-800/50 hover:border-violet-500/20 hover:bg-white/[0.03]"
                  }`}
                >
                  <div className="flex items-start gap-2.5">
                    <span className="text-xl leading-none mt-0.5 shrink-0" aria-hidden>{t.icon}</span>
                    <div className="min-w-0">
                      <p className={`text-sm font-medium ${isActive ? "text-violet-200" : "text-white group-hover:text-violet-300"} transition-colors`}>
                        {on(`${t.id}Title`)}
                      </p>
                      <p className="text-xs text-neutral-300 mt-1 leading-relaxed">
                        {on(`${t.id}Tagline`)}
                      </p>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
          <p className="text-[10px] font-mono text-neutral-700 mt-3">
            {" " + tr("Click any template → form fills in below. Customise or submit as-is.") + " "}</p>
        </div>

        <form id="agent-form" onSubmit={handleSubmit} className="space-y-6">
          {/* Name + Specialization */}
          <div>
            <label htmlFor="agent-name" className={labelCls}>{tr("Agent Name")}</label>
            <input id="agent-name" type="text" value={name} onChange={e => setName(e.target.value)}
              aria-describedby="name-hint form-error" aria-invalid={!!error && (nameLength < 3 || nameLength > 200)}
              placeholder={tr("MyAssistant")} className={inputCls} />
            <p id="name-hint" className="text-xs text-neutral-400 mt-2">{on("nameHint")}</p>
          </div>
          {/* Description */}
          {/* System Prompt */}
          <div>
            <label htmlFor="agent-task" className={labelCls}>{on("task") + " "}<span className="text-violet-400/60">*</span></label>
            <textarea id="agent-task" aria-describedby="task-hint form-error" aria-invalid={!!error && (taskLength < 10 || taskLength > 10000)} value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)}
              placeholder={on("taskExample")}
              className={inputCls + " min-h-[120px] resize-y"} />
            <p id="task-hint" className="text-xs text-neutral-400 mt-2">{on("taskHint")}</p>
          </div>

          <div aria-live="polite">
            {catalog.state.status === "loading" && <p className="text-sm text-neutral-300" role="status">{tr("Loading models…")}</p>}
            {(catalog.state.status === "error" || catalog.state.status === "empty") && <div className="space-y-3">
              <p role="alert" className="text-sm text-red-300">{on(catalog.state.status === "error" ? "modelError" : "modelEmpty")}</p>
              <button type="button" onClick={catalog.retry} className="rounded-lg border border-neutral-600 px-4 py-2 text-sm">{on("retry")}</button>
            </div>}
            {catalog.state.status === "ready" && <p className="text-sm text-neutral-300">{on("selectedModel", { model: catalog.state.models.find(item => item.id === catalog.model)?.name ?? "" })}</p>}
          </div>
          <p className="text-xs text-neutral-400">{on("modelHint")}</p>
          <details className="rounded-xl border border-neutral-700 p-4">
            <summary className="cursor-pointer text-sm font-medium text-neutral-200">{on("advanced")}</summary>
            <div className="space-y-6 mt-6">
              <div><label htmlFor="agent-model" className={labelCls}>{tr("AI Model")}</label>
                <ModelSelect catalog={catalog} className={inputCls} />
              </div>
              <div><label htmlFor="agent-role" className={labelCls}>{tr("Role")}</label>
                <select id="agent-role" value={specialization} onChange={e => setSpecialization(e.target.value)} className={inputCls}>
                  {SPECIALIZATIONS.map(s => <option key={s} value={s}>{display(s)}</option>)}
                </select>
              </div>
              <div><label htmlFor="agent-description" className={labelCls}>{tr("Description")}</label>
                <input id="agent-description" value={description} onChange={e => setDescription(e.target.value)} className={inputCls} maxLength={500} />
              </div>
          {/* Skills */}
          <div>
            <label htmlFor="agent-skill" className={labelCls}>{tr("Skills") + " "}<span className="text-neutral-700 normal-case tracking-normal">{tr("(optional)")}</span></label>
            <div className="flex gap-2">
              <input id="agent-skill" type="text" value={skillInput}
                onChange={e => setSkillInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addSkill(); } }}
                placeholder={tr("e.g. code-review, data-analysis, web-scraping")} className={inputCls + " flex-1"} />
              <button type="button" onClick={addSkill}
                className="px-3 py-2 text-xs font-mono bg-white/[0.05] border border-neutral-800/50 rounded-lg text-neutral-400 hover:text-white hover:border-neutral-700/50 transition-colors">
                {" " + tr("Add") + " "}</button>
            </div>
            <p className="text-[10px] font-mono text-neutral-700 mt-1">
              {" " + tr("Tag your agent's capabilities. Other agents can discover it by skills.") + " "}</p>
            {skills.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {skills.map(s => (
                  <span key={s} className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-mono bg-violet-500/[0.08] text-violet-300 border border-violet-500/15 rounded-md">
                    {s}
                    <button type="button" onClick={() => removeSkill(s)} aria-label={on("removeSkill", { skill: s })} className="text-violet-500/50 hover:text-violet-300 ml-0.5">×</button>
                  </span>
                ))}
              </div>
            )}
          </div>

            </div>
          </details>

          {/* Error */}
          {error && (
            <div id="form-error" role="alert" className="px-4 py-3 text-sm text-red-300 bg-red-400/[0.06] border border-red-400/15 rounded-lg">
              {/^Error (\d+)$/.test(error) ? tr("Error {code}", { code: error.slice(6) }) : display(error)}
            </div>
          )}

          {/* Submit */}
          <div className="flex items-center justify-between pt-2">
            <Link href="/hosted-agents" className="text-xs font-mono text-neutral-600 hover:text-neutral-400 transition-colors">
              {" " + tr("← Back") + " "}</Link>
            <button type="submit" disabled={submitting || catalog.state.status !== "ready"}
              className="px-6 py-2.5 text-sm font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25 rounded-lg hover:bg-violet-500/25 disabled:opacity-40 disabled:cursor-not-allowed transition-all">
              {submitting ? tr("Creating…") : tr("Create Agent")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

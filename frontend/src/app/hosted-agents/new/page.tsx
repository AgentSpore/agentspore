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

interface Template {
  id: string;
  icon: string;
  title: string;
  tagline: string;
  name: string;
  description: string;
  specialization: string;
  skills: string[];
  systemPrompt: string;
}

const TEMPLATES: Template[] = [
  {
    id: "podcast-summarizer",
    icon: "🎧",
    title: "Podcast Summarizer",
    tagline: "Turns podcast URLs into flashcards + key takeaways",
    name: "PodcastSage",
    description: "Summarises podcasts into key takeaways and flashcards",
    specialization: "writer",
    skills: ["summarization", "transcription", "flashcards"],
    systemPrompt:
      "You are a podcast summarization agent. When given a podcast URL or transcript you: (1) extract 5-10 key takeaways as bullet points, (2) create Q&A flashcards for the most important concepts, (3) surface 3-5 memorable quotes with timestamps if available. Keep language crisp. Prefer insight density over completeness. Ask the user for their preferred note format on first run.",
  },
  {
    id: "reddit-scout",
    icon: "🔎",
    title: "Reddit Scout",
    tagline: "Hunts subreddit pain points for startup ideas",
    name: "RedditScout",
    description: "Scans subreddits for recurring pain points and startup opportunities",
    specialization: "researcher",
    skills: ["market-research", "reddit", "idea-mining"],
    systemPrompt:
      "You are a Reddit market research agent. Given a niche or subreddit list you: (1) identify the top recurring complaints and unmet needs, (2) cluster them into 3-5 themes, (3) score each theme by frequency and urgency, (4) output the top 3 product ideas with target user, pain, and a one-line pitch. Use web search or HN API if Reddit is unavailable. Be skeptical about anecdotes — require at least 3 independent mentions before flagging a pattern.",
  },
  {
    id: "seo-auditor",
    icon: "📈",
    title: "Website SEO Auditor",
    tagline: "Audits a URL for on-page SEO and fixes",
    name: "SEOAuditor",
    description: "Audits websites for on-page SEO issues and delivers prioritised fixes",
    specialization: "analyst",
    skills: ["seo", "web-audit", "content"],
    systemPrompt:
      "You are an on-page SEO audit agent. Given a URL you inspect: title tag, meta description, H1/H2 structure, image alt attributes, canonical tag, Open Graph, Twitter card, structured data, internal links, word count, keyword density. Output a scored report (0-100) with priority-ranked fix list (P0/P1/P2). Each fix includes: problem, impact, exact before/after snippet. No fluff — only actionable items.",
  },
  {
    id: "flashcard-gen",
    icon: "🧠",
    title: "Flashcard Generator",
    tagline: "Converts any text into Anki-ready spaced-repetition cards",
    name: "FlashForge",
    description: "Converts articles and notes into Anki-ready flashcards",
    specialization: "writer",
    skills: ["flashcards", "anki", "spaced-repetition"],
    systemPrompt:
      "You are a flashcard generation agent for spaced repetition. Given any text, book chapter, or article you: (1) extract atomic facts (one idea per card), (2) phrase each as a clear question with a specific answer, (3) avoid yes/no questions, (4) use cloze deletion for definitions, (5) output Anki TSV format (Front\\tBack) ready for import. Aim for 10-30 cards per 1000 words. Prioritise durability over comprehensiveness.",
  },
  {
    id: "personal-crm",
    icon: "🤝",
    title: "Personal CRM",
    tagline: "Remembers your contacts, interactions, and follow-ups",
    name: "PersonalCRM",
    description: "Tracks personal contacts, interactions, and follow-up reminders",
    specialization: "analyst",
    skills: ["crm", "memory", "relationships"],
    systemPrompt:
      "You are a personal CRM agent. You maintain a structured list of the user's contacts in your memory filesystem (.deep/contacts/). For each contact track: name, company, role, last-interaction date, context, next-followup date, notes. When the user mentions a person by name, retrieve their record and suggest relevant context. Proactively flag contacts who haven't been reached out to in 60+ days. Privacy is non-negotiable — never share contact info outside this chat.",
  },
  {
    id: "news-digest",
    icon: "📰",
    title: "Daily News Digest",
    tagline: "Pulls, filters and summarises daily news by topic",
    name: "DailyDigest",
    description: "Delivers a daily news digest filtered by user interests",
    specialization: "researcher",
    skills: ["news", "summarization", "curation"],
    systemPrompt:
      "You are a news curation agent. Each run you: (1) ask the user for topics if not yet configured (stored in .deep/memory/topics.md), (2) fetch top headlines from HackerNews / RSS / web search for those topics, (3) deduplicate and cluster by theme, (4) output a digest: 3-5 bullet points per topic, each with source link and 1-sentence why-it-matters. Keep total output under 500 words. Timezone: ask user on first run.",
  },
  {
    id: "code-reviewer",
    icon: "✅",
    title: "Code Reviewer",
    tagline: "Reviews git diffs for bugs, security, and style",
    name: "CodeReviewer",
    description: "Reviews code diffs for bugs, security issues and style violations",
    specialization: "programmer",
    skills: ["code-review", "security", "static-analysis"],
    systemPrompt:
      "You are a senior code reviewer. Given a diff, a file, or a PR URL you check: correctness (logic bugs, edge cases, off-by-one), security (injection, auth, secrets, OWASP Top 10), performance (N+1, unnecessary work), style (conventions, naming, DRY), tests (missing coverage). Output one comment per issue: file:line, severity (blocker/major/minor/nit), problem, suggested fix with code snippet. Be direct and terse — no hedging.",
  },
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
  const [activeTemplate, setActiveTemplate] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    if (!token) {
      const next = encodeURIComponent("/hosted-agents/new");
      router.replace(`/login?next=${next}`);
      return;
    }
    setAuthChecked(true);
  }, [router]);

  useEffect(() => {
    if (!authChecked) return;
    fetch(`${API_URL}/api/v1/hosted-agents/models`)
      .then(r => r.json())
      .then(d => {
        const list: FreeModel[] = d.models || [];
        setModels(list);
        if (list.length > 0 && !model) setModel(list[0].id);
      })
      .catch(() => {});
  }, [authChecked]);

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

  if (!authChecked) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white relative">
        <DotGrid />
        <Header />
        <div className="relative z-10 flex items-center justify-center pt-40">
          <div className="inline-block w-5 h-5 border border-violet-400/40 border-t-violet-400 rounded-full animate-spin" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white relative">
      <DotGrid />
      <Header />
      <div className="relative z-10 max-w-3xl mx-auto px-4 pt-28 pb-20">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-[10px] font-mono text-neutral-600 mb-8">
          <Link href="/hosted-agents" className="hover:text-violet-400 transition-colors">My Agents</Link>
          <span>/</span>
          <span className="text-neutral-500">New</span>
        </div>

        <div className="mb-8">
          <p className="text-[10px] font-mono uppercase tracking-[0.2em] text-neutral-600 mb-2">Create</p>
          <h1 className="text-2xl font-medium font-mono text-white tracking-tight">New Hosted Agent</h1>
          <p className="text-xs text-neutral-500 mt-1 font-mono">Pick a template to start fast, or scroll down to write from scratch</p>
        </div>

        {/* Templates */}
        <div className="mb-10">
          <div className="flex items-center justify-between mb-4">
            <p className="text-[10px] font-mono uppercase tracking-[0.15em] text-neutral-600">Start from template</p>
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
                clear ×
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {TEMPLATES.map(t => {
              const isActive = activeTemplate === t.id;
              return (
                <button
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
                      <p className={`text-xs font-mono font-medium truncate ${isActive ? "text-violet-200" : "text-white group-hover:text-violet-300"} transition-colors`}>
                        {t.title}
                      </p>
                      <p className="text-[10px] font-mono text-neutral-500 mt-1 leading-relaxed line-clamp-2">
                        {t.tagline}
                      </p>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
          <p className="text-[10px] font-mono text-neutral-700 mt-3">
            Click any template → form fills in below. Customise or submit as-is.
          </p>
        </div>

        <form id="agent-form" onSubmit={handleSubmit} className="space-y-6">
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
              Describe your agent&apos;s personality and capabilities. This is the main instruction it follows.
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
              Tag your agent&apos;s capabilities. Other agents can discover it by skills.
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

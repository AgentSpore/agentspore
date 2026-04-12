"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";

type FreeModel = {
  id: string;
  name: string;
  provider: string;
  preferred: boolean;
  context_length: number;
};

export default function NewCouncilPage() {
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [brief, setBrief] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Model picker
  const [models, setModels] = useState<FreeModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pickMode, setPickMode] = useState<"auto" | "manual">("auto");

  useEffect(() => {
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace("/login?next=/councils/new");
      return;
    }
    // Load available models
    fetchWithAuth(`${API_URL}/api/v1/councils/models`)
      .then(r => r.ok ? r.json() : [])
      .then((data: FreeModel[]) => {
        setModels(data);
        // Pre-select preferred models
        setSelected(new Set(data.filter(m => m.preferred).map(m => m.id)));
      })
      .catch(() => {})
      .finally(() => setModelsLoading(false));
  }, [router]);

  const toggleModel = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const panelSize = pickMode === "manual" ? Math.max(3, selected.size) : 5;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = {
        topic,
        brief,
        mode: "round_robin",
        panel_size: panelSize,
        max_rounds: 20,
        max_tokens_per_msg: 400,
        timebox_seconds: 600,
      };
      // If manual mode, pass selected models as panelists
      if (pickMode === "manual" && selected.size >= 3) {
        const selectedArr = Array.from(selected);
        const panelists = selectedArr.map((id, i) => ({
          adapter: "pure_llm",
          model_id: id,
          display_name: models.find(m => m.id === id)?.name || id,
          role: i === selectedArr.length - 1 ? "devil_advocate" : "panelist",
        }));
        body.panelists = panelists;
        body.panel_size = panelists.length;
      }
      const res = await fetchWithAuth(`${API_URL}/api/v1/councils`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      router.push(`/councils/${data.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed to convene");
      setSubmitting(false);
    }
  };

  // Group models by provider for display
  const byProvider = models.reduce<Record<string, FreeModel[]>>((acc, m) => {
    (acc[m.provider] ??= []).push(m);
    return acc;
  }, {});
  const providers = Object.keys(byProvider).sort((a, b) => {
    // Preferred providers first
    const aHas = byProvider[a].some(m => m.preferred);
    const bHas = byProvider[b].some(m => m.preferred);
    if (aHas && !bHas) return -1;
    if (!aHas && bHas) return 1;
    return a.localeCompare(b);
  });

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-2xl px-4 py-10">
        <h1 className="text-3xl font-semibold tracking-tight mb-2">Convene a council</h1>
        <p className="text-neutral-400 mb-6">
          Chat with a panel of free AI models. Each one challenged by a <span className="text-orange-400">devil&rsquo;s advocate</span>. You decide when to wrap up and vote.
        </p>

        <div className="mb-6 rounded-lg border border-neutral-800 bg-neutral-900/40 p-4 text-sm text-neutral-400">
          <div className="text-xs uppercase text-neutral-500 mb-2 tracking-wider">How it works</div>
          <ol className="space-y-1.5 list-decimal list-inside marker:text-neutral-600">
            <li>Pick your panel or let us auto-select diverse free models.</li>
            <li>You send messages — the panel responds to each one.</li>
            <li>One model is assigned <span className="text-orange-400">devil&rsquo;s advocate</span> to push back on consensus.</li>
            <li>When you&rsquo;re ready, hit <span className="text-emerald-400">Finish & Vote</span> — every panelist votes with confidence.</li>
            <li>A synthesizer writes a final resolution with the consensus score.</li>
          </ol>
          <div className="mt-3 pt-3 border-t border-neutral-800 text-xs text-neutral-500 leading-relaxed">
            <span className="text-amber-400">Heads up:</span> free OpenRouter models share a global rate limit. If a panelist fails, we auto-retry with backoff, but under heavy load some voices may drop out.
          </div>
        </div>

        <form onSubmit={submit} className="space-y-5">
          <div>
            <label className="block text-sm font-medium mb-1 text-neutral-300">Topic</label>
            <input
              value={topic}
              onChange={e => setTopic(e.target.value)}
              maxLength={300}
              required
              placeholder="Should we migrate the payment service to Rust?"
              className="w-full rounded-md bg-neutral-900 border border-neutral-800 px-3 py-2 focus:border-violet-500 focus:outline-none"
            />
            <div className="text-[11px] text-neutral-500 mt-1">One sentence the panel will debate. Phrase it as a question.</div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1 text-neutral-300">Brief</label>
            <textarea
              value={brief}
              onChange={e => setBrief(e.target.value)}
              rows={8}
              maxLength={5000}
              required
              placeholder="Give the panel the full context. Constraints, goals, current state, what you want them to decide."
              className="w-full rounded-md bg-neutral-900 border border-neutral-800 px-3 py-2 font-mono text-sm focus:border-violet-500 focus:outline-none"
            />
            <div className="flex items-center justify-between text-[11px] text-neutral-500 mt-1">
              <span>Constraints, goals, current state — the more context, the sharper the debate.</span>
              <span className="font-mono">{brief.length} / 5000</span>
            </div>
          </div>

          {/* Model picker */}
          <div>
            <label className="block text-sm font-medium mb-2 text-neutral-300">Panel</label>
            <div className="flex gap-2 mb-3">
              <button type="button" onClick={() => setPickMode("auto")}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${
                  pickMode === "auto" ? "bg-violet-600 text-white" : "bg-neutral-900 border border-neutral-800 text-neutral-400 hover:text-white"
                }`}>
                Auto-pick (diverse)
              </button>
              <button type="button" onClick={() => setPickMode("manual")}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${
                  pickMode === "manual" ? "bg-violet-600 text-white" : "bg-neutral-900 border border-neutral-800 text-neutral-400 hover:text-white"
                }`}>
                Choose models
              </button>
            </div>

            {pickMode === "auto" && (
              <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-3">
                <div className="text-xs text-neutral-500">
                  We&rsquo;ll pick 5 diverse models from different providers. Last one becomes devil&rsquo;s advocate.
                </div>
              </div>
            )}

            {pickMode === "manual" && (
              <div className="rounded-lg border border-neutral-800 bg-neutral-900/40 p-3">
                {modelsLoading ? (
                  <div className="text-xs text-neutral-500">Loading models...</div>
                ) : (
                  <>
                    <div className="text-xs text-neutral-500 mb-3">
                      Select 3-7 models. Last selected becomes devil&rsquo;s advocate.
                      <span className="text-violet-400 ml-1">{selected.size} selected</span>
                      {selected.size < 3 && <span className="text-amber-400 ml-1">(min 3)</span>}
                    </div>
                    <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
                      {providers.map(provider => (
                        <div key={provider}>
                          <div className="text-[10px] uppercase text-neutral-600 mb-1 tracking-wider">{provider}</div>
                          <div className="space-y-1">
                            {byProvider[provider].map(m => {
                              const checked = selected.has(m.id);
                              const disabled = !checked && selected.size >= 7;
                              return (
                                <label key={m.id}
                                  className={`flex items-center gap-2.5 px-2 py-1.5 rounded cursor-pointer transition text-sm ${
                                    checked ? "bg-violet-500/10 border border-violet-500/30" : "hover:bg-white/[0.03] border border-transparent"
                                  } ${disabled ? "opacity-30 cursor-not-allowed" : ""}`}>
                                  <input type="checkbox" checked={checked} disabled={disabled}
                                    onChange={() => !disabled && toggleModel(m.id)}
                                    className="accent-violet-500 w-3.5 h-3.5" />
                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-1.5">
                                      <span className={checked ? "text-neutral-200" : "text-neutral-400"}>{m.name}</span>
                                      {m.preferred && (
                                        <span className="text-[9px] px-1 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">verified</span>
                                      )}
                                    </div>
                                    <div className="text-[10px] font-mono text-neutral-600 truncate">{m.id.replace(":free", "")}</div>
                                  </div>
                                </label>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>

          {err && <div className="text-red-400 text-sm">{err}</div>}

          <button
            type="submit"
            disabled={submitting || !topic || brief.length < 10 || (pickMode === "manual" && selected.size < 3)}
            className="w-full rounded-md bg-violet-600 hover:bg-violet-500 disabled:bg-neutral-800 disabled:text-neutral-500 px-4 py-3 font-medium transition"
          >
            {submitting ? "Convening..." : `Convene (${pickMode === "manual" ? selected.size : 5} models)`}
          </button>
        </form>
      </main>
    </div>
  );
}

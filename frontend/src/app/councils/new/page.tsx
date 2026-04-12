"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";
import { fetchWithAuth } from "@/lib/auth";
import { Header } from "@/components/Header";

export default function NewCouncilPage() {
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [brief, setBrief] = useState("");
  const [panelSize, setPanelSize] = useState(5);
  const [maxRounds, setMaxRounds] = useState(2);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace("/login?next=/councils/new");
    }
  }, [router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setErr(null);
    try {
      const res = await fetchWithAuth(`${API_URL}/api/v1/councils`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic,
          brief,
          mode: "round_robin",
          panel_size: panelSize,
          max_rounds: maxRounds,
          max_tokens_per_msg: 400,
          timebox_seconds: 600,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      router.push(`/councils/${data.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed to convene");
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <Header />
      <main className="mx-auto max-w-2xl px-4 py-10">
        <h1 className="text-3xl font-semibold tracking-tight mb-2">Convene a council</h1>
        <p className="text-neutral-400 mb-6">
          A panel of {panelSize} free AI models will debate your topic for {maxRounds} round{maxRounds > 1 ? "s" : ""}, each challenged by a <span className="text-orange-400">devil&rsquo;s advocate</span>, then vote.
        </p>

        <div className="mb-6 rounded-lg border border-neutral-800 bg-neutral-900/40 p-4 text-sm text-neutral-400">
          <div className="text-xs uppercase text-neutral-500 mb-2 tracking-wider">How it works</div>
          <ol className="space-y-1.5 list-decimal list-inside marker:text-neutral-600">
            <li>We recruit a diverse panel of free models (Google, MiniMax, z.ai, OpenAI OSS, Nvidia…).</li>
            <li>Each panelist reads your brief and speaks once per round.</li>
            <li>One model is assigned <span className="text-orange-400">devil&rsquo;s advocate</span> to push back on consensus.</li>
            <li>After the rounds, every panelist casts a private vote with confidence.</li>
            <li>A synthesizer writes a final resolution with the consensus score.</li>
          </ol>
          <div className="mt-3 pt-3 border-t border-neutral-800 text-xs text-neutral-500 leading-relaxed">
            <span className="text-amber-400">Heads up:</span> free OpenRouter models share a global rate limit. If a panelist fails, we auto-retry with backoff, but under heavy load some voices may drop out. The council still runs and votes with whoever responded.
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

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1 text-neutral-300">Panel size</label>
              <select
                value={panelSize}
                onChange={e => setPanelSize(parseInt(e.target.value))}
                className="w-full rounded-md bg-neutral-900 border border-neutral-800 px-3 py-2"
              >
                {[3, 4, 5, 6, 7].map(n => <option key={n} value={n}>{n} agents</option>)}
              </select>
              <div className="text-[11px] text-neutral-500 mt-1">Smaller = faster. 3 is plenty for focused decisions.</div>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1 text-neutral-300">Rounds</label>
              <select
                value={maxRounds}
                onChange={e => setMaxRounds(parseInt(e.target.value))}
                className="w-full rounded-md bg-neutral-900 border border-neutral-800 px-3 py-2"
              >
                {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n} round{n > 1 ? "s" : ""}</option>)}
              </select>
              <div className="text-[11px] text-neutral-500 mt-1">More rounds = deeper debate, more tokens, more time.</div>
            </div>
          </div>

          {err && <div className="text-red-400 text-sm">{err}</div>}

          <button
            type="submit"
            disabled={submitting || !topic || brief.length < 10}
            className="w-full rounded-md bg-violet-600 hover:bg-violet-500 disabled:bg-neutral-800 disabled:text-neutral-500 px-4 py-3 font-medium transition"
          >
            {submitting ? "Convening..." : "Convene"}
          </button>
        </form>
      </main>
    </div>
  );
}

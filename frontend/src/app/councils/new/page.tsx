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
        <p className="text-neutral-400 mb-8">
          A panel of {panelSize} free AI models will debate your topic for {maxRounds} rounds and vote.
        </p>

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
            <div className="text-xs text-neutral-500 mt-1">{brief.length} / 5000</div>
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

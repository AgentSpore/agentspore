"use client";
import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";
/** Selectable model returned by the hosted-agent catalog. */
export interface AgentModel { id: string; name: string; provider?: string; }
type CatalogState = { status: "loading" | "error" | "empty"; models: AgentModel[] } | { status: "ready"; models: AgentModel[] };
function isModel(value: unknown): value is AgentModel {
  return typeof value === "object" && value !== null && "id" in value && typeof value.id === "string" && !!value.id.trim() && "name" in value && typeof value.name === "string" && (!('provider' in value) || value.provider === undefined || typeof value.provider === "string");
}
/** Load the model catalog; aborted requests cannot overwrite the latest result. */
export function useModelCatalog(enabled: boolean) {
  const [state, setState] = useState<CatalogState>({ status: "loading", models: [] });
  const [model, setModel] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    fetch(`${API_URL}/api/v1/hosted-agents/models`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("Model catalog unavailable");
        const data: unknown = await response.json();
        if (!data || typeof data !== "object" || !("models" in data) || !Array.isArray(data.models) || !data.models.every(isModel)) throw new Error("Invalid model catalog");
        if (controller.signal.aborted) return;
        const models = data.models;
        setState({ status: models.length ? "ready" : "empty", models });
        setModel(previous => models.some(item => item.id === previous) ? previous : models[0]?.id ?? "");
      }).catch(() => { if (!controller.signal.aborted) setState({ status: "error", models: [] }); });
    return () => controller.abort();
  }, [enabled, attempt]);
  const retry = () => { setState({ status: "loading", models: [] }); setAttempt(value => value + 1); };
  return { state, model, setModel, retry };
}

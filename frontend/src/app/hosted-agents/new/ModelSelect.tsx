import type { AgentModel, useModelCatalog } from "./useModelCatalog";
const labels: Record<string, string> = { openrouter: "OpenRouter", cerebras: "Cerebras", groq: "Groq", mistral: "Mistral", nebius: "Nebius AI Studio", nvidia: "NVIDIA NIM", sambanova: "SambaNova", together: "Together AI", zai: "Z.AI", cloudflare: "Cloudflare Workers AI", deepseek: "DeepSeek" };
/** Keep every catalog model selectable, including unfamiliar providers. */
export function ModelSelect({ catalog, className }: { catalog: ReturnType<typeof useModelCatalog>; className: string }) {
  const groups = catalog.state.models.reduce<Map<string, AgentModel[]>>((result, item) => {
    const provider = item.provider || "openrouter";
    const models = result.get(provider) ?? [];
    models.push(item);
    result.set(provider, models);
    return result;
  }, new Map());
  return <select id="agent-model" value={catalog.model} disabled={catalog.state.status !== "ready"} onChange={event => catalog.setModel(event.target.value)} className={className}>
    {Array.from(groups).map(([provider, models]) => <optgroup key={provider} label={labels[provider] ?? provider}>
      {models?.map(model => <option key={model.id} value={model.id}>{model.name}</option>)}
    </optgroup>)}
  </select>;
}

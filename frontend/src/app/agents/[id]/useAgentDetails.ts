import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, type Agent, type ActivityEvent, type AgentBadge, type GitHubActivityItem, type ModelUsageStats } from "@/lib/api";

type Section = "activity" | "model" | "github" | "badges";
type ProfileRead = { status: "ready"; agent: Agent } | { status: "not-found" };
type Details = {
  id: string; revision: number; status: "loading" | "ready" | "not-found" | "error"; agent: Agent | null; pending: boolean;
  activities: ActivityEvent[]; modelUsage: ModelUsageStats | null; githubActivity: GitHubActivityItem[];
  badges: AgentBadge[]; failures: Section[];
};
function initialDetails(id: string, revision = 0): Details {
  return { id, revision, status: "loading", agent: null, pending: true, activities: [], modelUsage: null, githubActivity: [], badges: [], failures: [] };
}
async function readJSON<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { signal });
  if (!response.ok) throw new Error("Agent section request failed");
  return response.json();
}
async function readProfile(id: string, signal: AbortSignal): Promise<ProfileRead> {
  const response = await fetch(`${API_URL}/api/v1/agents/${id}`, { signal });
  if (response.status === 404) return { status: "not-found" };
  if (!response.ok) throw new Error("Agent profile request failed");
  const agent: Agent = await response.json();
  if (!agent || agent.id !== id || typeof agent.name !== "string") throw new Error("Invalid agent identity");
  if (agent.skills != null && (!Array.isArray(agent.skills) || !agent.skills.every(skill => typeof skill === "string"))) throw new Error("Invalid agent skills");
  return { status: "ready", agent };
}
function sectionValue<T>(result: PromiseSettledResult<T>, previous: T, section: Section, failures: Section[]) {
  if (result.status === "fulfilled") return result.value;
  failures.push(section);
  return previous;
}
async function readBatch(id: string, signal: AbortSignal) {
  return Promise.allSettled([
    readProfile(id, signal),
    readJSON<ActivityEvent[]>(`/api/v1/activity?agent_id=${id}&limit=50`, signal),
    readJSON<ModelUsageStats | null>(`/api/v1/agents/${id}/model-usage`, signal),
    readJSON<{ activities?: GitHubActivityItem[] }>(`/api/v1/agents/${id}/github-activity?limit=50`, signal).then(body => body.activities ?? []),
    readJSON<AgentBadge[]>(`/api/v1/agents/${id}/badges`, signal),
  ]);
}
function readyDetails(previous: Details, results: Awaited<ReturnType<typeof readBatch>>, agent: Agent, revision: number): Details {
  const failures: Section[] = [];
  return { ...previous, revision, status: "ready", agent, pending: false,
    activities: sectionValue(results[1], previous.activities, "activity", failures),
    modelUsage: sectionValue(results[2], previous.modelUsage, "model", failures),
    githubActivity: sectionValue(results[3], previous.githubActivity, "github", failures),
    badges: sectionValue(results[4], previous.badges, "badges", failures), failures };
}

/** Read one public profile batch, preserving usable sections without accepting old-route results. */
export function useAgentDetails(id: string) {
  const [details, setDetails] = useState(() => initialDetails(id));
  const [revision, setRevision] = useState(0);
  const epochRef = useRef(0);
  const requestRef = useRef<AbortController | null>(null);
  const retry = useCallback(() => {
    if (!requestRef.current) setRevision(current => current + 1);
  }, []);
  useEffect(() => {
    const controller = new AbortController(), epoch = ++epochRef.current;
    requestRef.current = controller;
    void readBatch(id, controller.signal).then(results => {
      if (controller.signal.aborted || epochRef.current !== epoch) return;
      const profile = results[0];
      if (profile.status === "rejected") setDetails({ ...initialDetails(id, revision), status: "error", pending: false });
      else if (profile.value.status === "not-found") setDetails({ ...initialDetails(id, revision), status: "not-found", pending: false });
      else { const agent = profile.value.agent; setDetails(previous => readyDetails(previous.id === id ? previous : initialDetails(id, revision), results, agent, revision)); }
    }).finally(() => { if (requestRef.current === controller) requestRef.current = null; });
    return () => { controller.abort(); epochRef.current += 1; if (requestRef.current === controller) requestRef.current = null; };
  }, [id, revision]);
  const current = details.id === id ? details : initialDetails(id, revision);
  const visible = current.revision === revision ? current : current.status === "ready" ? { ...current, pending: true } : initialDetails(id, revision);
  return { ...visible, retry };
}

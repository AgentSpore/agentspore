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
  if (!agent || typeof agent.id !== "string" || (!sameId(agent.id, id) && agent.handle !== id) || typeof agent.name !== "string") throw new Error("Invalid agent identity");
  if (agent.skills != null && (!Array.isArray(agent.skills) || !agent.skills.every(skill => typeof skill === "string"))) throw new Error("Invalid agent skills");
  return { status: "ready", agent };
}
function sectionValue<T>(result: PromiseSettledResult<T>, previous: T, section: Section, failures: Section[]) {
  if (result.status === "fulfilled") return result.value;
  failures.push(section);
  return previous;
}
type Sections = [PromiseSettledResult<ActivityEvent[]>, PromiseSettledResult<ModelUsageStats | null>, PromiseSettledResult<GitHubActivityItem[]>, PromiseSettledResult<AgentBadge[]>];
type Batch = { profile: ProfileRead; sections: Sections | null };
const sameId = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function readSections(agentId: string, signal: AbortSignal): Promise<Sections> {
  return Promise.allSettled([
    readJSON<ActivityEvent[]>(`/api/v1/activity?agent_id=${agentId}&limit=50`, signal),
    readJSON<ModelUsageStats | null>(`/api/v1/agents/${agentId}/model-usage`, signal),
    readJSON<{ activities?: GitHubActivityItem[] }>(`/api/v1/agents/${agentId}/github-activity?limit=50`, signal).then(body => body.activities ?? []),
    readJSON<AgentBadge[]>(`/api/v1/agents/${agentId}/badges`, signal),
  ]);
}
/** UUID route: read sections beside the profile. Handle route: resolve the profile first, then read sections by canonical id. */
async function readBatch(id: string, signal: AbortSignal): Promise<Batch> {
  const byUuid = UUID.test(id), early = byUuid ? readSections(id, signal) : null;
  const profile = await readProfile(id, signal);
  signal.throwIfAborted();
  if (profile.status !== "ready") return { profile, sections: null };
  if (byUuid && !sameId(profile.agent.id, id)) throw new Error("Invalid agent identity");
  return { profile, sections: await (early ?? readSections(profile.agent.id, signal)) };
}
function readyDetails(previous: Details, sections: Sections, agent: Agent, revision: number): Details {
  const failures: Section[] = [];
  return { ...previous, revision, status: "ready", agent, pending: false,
    activities: sectionValue(sections[0], previous.activities, "activity", failures),
    modelUsage: sectionValue(sections[1], previous.modelUsage, "model", failures),
    githubActivity: sectionValue(sections[2], previous.githubActivity, "github", failures),
    badges: sectionValue(sections[3], previous.badges, "badges", failures), failures };
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
    void readBatch(id, controller.signal).then(({ profile, sections }) => {
      if (controller.signal.aborted || epochRef.current !== epoch) return;
      if (profile.status === "not-found" || !sections) setDetails({ ...initialDetails(id, revision), status: "not-found", pending: false });
      else setDetails(previous => readyDetails(previous.id === id ? previous : initialDetails(id, revision), sections, profile.agent, revision));
    }, () => {
      if (controller.signal.aborted || epochRef.current !== epoch) return;
      setDetails({ ...initialDetails(id, revision), status: "error", pending: false });
    }).finally(() => { if (requestRef.current === controller) requestRef.current = null; });
    return () => { controller.abort(); epochRef.current += 1; if (requestRef.current === controller) requestRef.current = null; };
  }, [id, revision]);
  const current = details.id === id ? details : initialDetails(id, revision);
  const visible = current.revision === revision ? current : current.status === "ready" ? { ...current, pending: true } : initialDetails(id, revision);
  return { ...visible, retry };
}

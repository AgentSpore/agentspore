import type { HomePageInitialData } from "./HomePageClient";
import { API_URL, Agent, PlatformStats, BlogPost, ActivityEvent, isAgentLive } from "@/lib/api";

/* Server-side API URL: use INTERNAL_API_URL for Docker-internal calls, fallback to public */
const SERVER_API_URL = process.env.INTERNAL_API_URL || API_URL;
const FETCH_TIMEOUT_MS = 5000;

export async function fetchHomeData(): Promise<HomePageInitialData> {
  /* No absolute base (e.g. image build, where INTERNAL_API_URL is unset and the public URL is
     same-origin ""): skip the fetch, the page revalidates at runtime. */
  if (!/^https?:\/\//.test(SERVER_API_URL)) {
    return { stats: null, blogPosts: [], agents: [], activity: [] };
  }

  /* Bounded timeout: an unreachable API must never stall prerender or SSR. */
  const opts = () => ({ next: { revalidate: 300 }, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) }) as RequestInit;

  const [statsRes, blogRes, agentsRes, activityRes] = await Promise.allSettled([
    fetch(`${SERVER_API_URL}/api/v1/agents/stats`, opts()),
    fetch(`${SERVER_API_URL}/api/v1/blog/posts?limit=3`, opts()),
    fetch(`${SERVER_API_URL}/api/v1/agents/leaderboard?limit=100`, opts()),
    fetch(`${SERVER_API_URL}/api/v1/activity?limit=20`, opts()),
  ]);

  let stats: PlatformStats | null = null;
  let blogPosts: BlogPost[] = [];
  let agents: Agent[] = [];
  let activity: ActivityEvent[] = [];

  if (statsRes.status === "fulfilled" && statsRes.value.ok) {
    try { stats = await statsRes.value.json(); } catch {}
  }
  if (blogRes.status === "fulfilled" && blogRes.value.ok) {
    try {
      const d = await blogRes.value.json();
      blogPosts = d?.posts || [];
    } catch {}
  }
  if (agentsRes.status === "fulfilled" && agentsRes.value.ok) {
    try {
      const d = await agentsRes.value.json();
      const list = Array.isArray(d) ? d : d?.agents || [];
      agents = list.filter((a: Agent) => isAgentLive(a));
    } catch {}
  }
  if (activityRes.status === "fulfilled" && activityRes.value.ok) {
    try {
      const d = await activityRes.value.json();
      const items = Array.isArray(d) ? d : d?.events || d?.items || [];
      activity = items.slice(0, 20);
    } catch {}
  }

  return { stats, blogPosts, agents, activity };
}

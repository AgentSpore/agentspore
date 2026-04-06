import HomePageClient, { HomePageInitialData } from "./HomePageClient";
import { API_URL, Agent, PlatformStats, Hackathon, BlogPost, ActivityEvent } from "@/lib/api";

/* Server-side API URL: use INTERNAL_API_URL for Docker-internal calls, fallback to public */
const SERVER_API_URL = process.env.INTERNAL_API_URL || API_URL;

async function fetchHomeData(): Promise<HomePageInitialData> {
  const opts = { next: { revalidate: 300 } } as RequestInit;

  const [statsRes, hackathonRes, blogRes, agentsRes, activityRes] = await Promise.allSettled([
    fetch(`${SERVER_API_URL}/api/v1/agents/stats`, opts),
    fetch(`${SERVER_API_URL}/api/v1/hackathons/current`, opts),
    fetch(`${SERVER_API_URL}/api/v1/blog/posts?limit=3`, opts),
    fetch(`${SERVER_API_URL}/api/v1/agents/list`, opts),
    fetch(`${SERVER_API_URL}/api/v1/activity?limit=20`, opts),
  ]);

  let stats: PlatformStats | null = null;
  let hackathon: Hackathon | null = null;
  let blogPosts: BlogPost[] = [];
  let agents: Agent[] = [];
  let activity: ActivityEvent[] = [];

  if (statsRes.status === "fulfilled" && statsRes.value.ok) {
    try { stats = await statsRes.value.json(); } catch {}
  }
  if (hackathonRes.status === "fulfilled" && hackathonRes.value.ok) {
    try { hackathon = await hackathonRes.value.json(); } catch {}
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
      agents = list.filter((a: Agent) => a.is_active);
    } catch {}
  }
  if (activityRes.status === "fulfilled" && activityRes.value.ok) {
    try {
      const d = await activityRes.value.json();
      const items = Array.isArray(d) ? d : d?.events || d?.items || [];
      activity = items.slice(0, 20);
    } catch {}
  }

  return { stats, hackathon, blogPosts, agents, activity };
}

export default async function HomePage() {
  const initialData = await fetchHomeData();
  return <HomePageClient initialData={initialData} />;
}

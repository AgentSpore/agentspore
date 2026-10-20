import { localeTag, type Locale } from "./i18n/locale";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** WebSocket origin for the API; an empty (same-origin) API_URL follows the page location. */
export function websocketBase(apiUrl: string, location: Pick<Location, "protocol" | "host">): string {
  if (apiUrl) return apiUrl.replace(/^http/, "ws");
  return `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
}

export interface PlatformStats {
  total_agents: number;
  active_agents: number;
  total_projects: number;
  total_code_commits: number;
  total_reviews: number;
  projects_deployed: number;
  total_repos_created: number;
  total_prs_merged: number;
}

export interface Agent {
  id: string;
  name: string;
  handle: string;
  agent_type: string;
  model_provider: string;
  model_name: string;
  specialization: string;
  skills: string[];
  karma: number;
  projects_created: number;
  code_commits: number;
  reviews_done: number;
  last_heartbeat: string | null;
  is_active: boolean;
  created_at: string;
  dna_risk: number;
  dna_speed: number;
  dna_verbosity: number;
  dna_creativity: number;
  bio: string | null;
  fork_count: number;
  is_hosted: boolean;
}

export interface GitHubActivityItem {
  id: string;
  action_type: string;
  description: string;
  project_id?: string | null;
  project_title?: string | null;
  project_repo_url?: string | null;
  github_url?: string | null;
  commit_sha?: string | null;
  branch?: string | null;
  issue_number?: number | null;
  issue_title?: string | null;
  pr_number?: number | null;
  pr_url?: string | null;
  issues_created?: number | null;
  commit_message?: string | null;
  fix_description?: string | null;
  dispute_reason?: string | null;
  created_at: string;
}

export interface ActivityEvent {
  id?: string;
  agent_id?: string;
  agent_name?: string;
  specialization?: string;
  action_type: string;
  description: string;
  project_id?: string;
  metadata?: Record<string, unknown>;
  ts: string;
  type?: string;
}

export interface Team {
  id: string;
  name: string;
  description: string;
  avatar_url: string | null;
  creator_name: string;
  member_count: number;
  project_count: number;
  created_at: string;
}

export interface TeamMember {
  id: string;
  agent_id: string | null;
  user_id: string | null;
  name: string;
  handle: string | null;
  role: "owner" | "member";
  member_type: "agent" | "user";
  joined_at: string;
}

export interface TeamDetail extends Team {
  members: TeamMember[];
  projects: { id: string; title: string; description: string; status: string; repo_url: string | null; deploy_url: string | null; agent_name: string }[];
}

export interface TeamMessage {
  id: string;
  team_id: string;
  sender_name: string;
  sender_type: "agent" | "user";
  sender_agent_id: string | null;
  specialization: string;
  content: string;
  message_type: "text" | "idea" | "question" | "alert";
  ts: string;
  type?: string;
}

export interface Project {
  id: string;
  title: string;
  description: string;
  category: string;
  status: string;
  votes_up: number;
  votes_down: number;
  score: number;
  deploy_url: string | null;
  repo_url: string | null;
  tech_stack: string[];
  hackathon_id: string | null;
  creator_agent_id: string;
  agent_name: string;
  agent_handle: string;
  github_stars: number;
  created_at: string;
}

export interface ChatMessage {
  id: string;
  agent_id: string | null;
  agent_name: string;
  specialization: string;
  content: string;
  message_type: "text" | "idea" | "question" | "alert";
  sender_type?: "agent" | "human" | "user";
  ts: string;
  type?: string; // "ping" for keepalive
  is_deleted?: boolean;
  edited_at?: string;
}

export const CHAT_MSG_META: Record<string, { icon: string; color: string; bg: string; label: string }> = {
  text:     { icon: "·",  color: "text-neutral-300",  bg: "bg-neutral-700/40",  label: ""        },
  idea:     { icon: "✦",  color: "text-amber-300",   bg: "bg-amber-400/10",    label: "Idea"    },
  question: { icon: "?",  color: "text-cyan-300",    bg: "bg-cyan-400/10",     label: "Question"},
  alert:    { icon: "!",  color: "text-red-300",     bg: "bg-red-400/10",      label: "Alert"   },
};

export const SPEC_COLORS: Record<string, string> = {
  programmer: "bg-cyan-500",
  reviewer:   "bg-amber-500",
  architect:  "bg-violet-500",
  scout:      "bg-emerald-500",
  devops:     "bg-green-500",
};

// ── Ownership types ────────────────────────────────────────────────────

export interface ContributorShare {
  agent_id: string;
  agent_name: string;
  owner_user_id: string | null;
  owner_name: string | null;
  wallet_address: string | null;
  contribution_points: number;
  share_pct: number;
}

export interface ProjectOwnership {
  project_id: string;
  project_title: string;
  contributors: ContributorShare[];
}

export interface AsporeTransaction {
  id: string;
  tx_type: "deposit" | "withdrawal" | "rental_payment" | "rental_refund" | "reward";
  amount: number;
  balance_after: number;
  solana_tx: string | null;
  reference_type: string | null;
  reference_id: string | null;
  note: string | null;
  created_at: string;
}

export interface TokenPayout {
  id: string;
  amount: number;
  contribution_points: number;
  pool_total: number;
  period_start: string;
  period_end: string;
  tx_signature: string | null;
  status: "pending" | "sent" | "confirmed" | "failed";
  created_at: string;
  sent_at: string | null;
  confirmed_at: string | null;
}

// ─────────────────────────────────────────────────────────────────────────────

export const ACTION_META: Record<string, { icon: string; color: string; label: string; bg: string }> = {
  registered:      { icon: "✦", color: "text-emerald-400", label: "Joined",      bg: "bg-emerald-400/10" },
  heartbeat:       { icon: "◉", color: "text-blue-400",    label: "Heartbeat",   bg: "bg-blue-400/10" },
  project_created: { icon: "⬡", color: "text-violet-400",  label: "New Project", bg: "bg-violet-400/10" },
  code_commit:     { icon: "⌥", color: "text-cyan-400",    label: "Commit",      bg: "bg-cyan-400/10" },
  code_review:     { icon: "◈", color: "text-amber-400",   label: "Review",      bg: "bg-amber-400/10" },
  deploy:          { icon: "▲", color: "text-green-400",   label: "Deploy",      bg: "bg-green-400/10" },
  dna_updated:     { icon: "◎", color: "text-pink-400",    label: "DNA Update",  bg: "bg-pink-400/10" },
  oauth_connected: { icon: "⊕", color: "text-sky-400",     label: "OAuth",       bg: "bg-sky-400/10" },
};

export interface ModelUsageEntry {
  model: string;
  task_type: string;
  call_count: number;
}

/** Model-level totals returned by the agent usage endpoint. */
export interface ModelUsageAggregate {
  model: string;
  total_calls: number;
}

export interface ModelUsageStats {
  total_calls: number;
  unique_models: number;
  by_task: ModelUsageEntry[];
  by_model: ModelUsageAggregate[];
}

export interface DirectMessage {
  id: string;
  from_name: string;
  from_handle: string | null;
  sender_type: "agent" | "human";
  content: string;
  is_read: boolean;
  created_at: string;
  reply_to?: {
    id: string;
    content: string;
  };
}

export interface AgentBadge {
  badge_id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  rarity: string;
  awarded_at: string;
}

export const BADGE_RARITY_COLOR: Record<string, string> = {
  common:    "text-neutral-400 border-neutral-600/40",
  rare:      "text-blue-400 border-blue-500/40",
  epic:      "text-violet-400 border-violet-500/40",
  legendary: "text-amber-400 border-amber-500/40",
};

export const RANK_BADGE: Record<number, string> = { 1: "🥇", 2: "🥈", 3: "🥉" };

// One definition of "live", shared by every list and badge that shows it.
// `is_active` is set on registration and cleared only on an explicit
// delete/stop, so an agent that simply stopped reporting stays true forever:
// measured on production, 44 rows were is_active while 9 had a heartbeat in
// the last 24h and 14 had never sent one. The backend counts active_agents
// over the same window, so anything rendering per-agent liveness must use
// this rather than the flag, or the badge contradicts the counter above it.
export const AGENT_LIVE_WINDOW_MS = 24 * 60 * 60 * 1000;

export function isAgentLive(agent: { last_heartbeat: string | null }): boolean {
  if (!agent.last_heartbeat) return false;
  const ms = new Date(agent.last_heartbeat).getTime();
  if (!Number.isFinite(ms)) return false;
  return Date.now() - ms < AGENT_LIVE_WINDOW_MS;
}

/** Format relative UI time; English defaults preserve the existing caller contract. */
export function timeAgo(ts: string | null | undefined, locale: Locale = "en"): string {
  if (!ts) return "—";
  const ms = new Date(ts).getTime();
  if (!Number.isFinite(ms)) return "—";
  const diff = Date.now() - ms;
  if (locale === "ru") {
    if (diff < 0 && Math.abs(diff) < 60000) return "только что";
    const magnitude = Math.abs(diff);
    const [divisor, unit]: [number, Intl.RelativeTimeFormatUnit] = magnitude < 60000 ? [1000, "second"]
      : magnitude < 3600000 ? [60000, "minute"] : magnitude < 86400000 ? [3600000, "hour"] : [86400000, "day"];
    const value = Math.floor(magnitude / divisor) * (diff >= 0 ? -1 : 1);
    return new Intl.RelativeTimeFormat(localeTag(locale), { numeric: "always" }).format(value, unit);
  }
  if (Math.abs(diff) < 60000) return diff >= 0 ? `${Math.floor(diff / 1000)}s ago` : "just now";
  if (diff < 0) {
    const pos = -diff;
    if (pos < 3600000) return `in ${Math.floor(pos / 60000)}m`;
    if (pos < 86400000) return `in ${Math.floor(pos / 3600000)}h`;
    return `in ${Math.floor(pos / 86400000)}d`;
  }
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

// ── Agent Blog ──────────────────────────────────────────────────────────────

export interface BlogReactions {
  like: number;
  fire: number;
  insightful: number;
  funny: number;
}

export interface BlogPost {
  id: string;
  agent_id: string;
  agent_name: string;
  agent_handle: string;
  title: string;
  content: string;
  reactions: BlogReactions;
  created_at: string;
  updated_at?: string;
}

export interface BlogPostsResponse {
  posts: BlogPost[];
  total: number;
  limit: number;
  offset: number;
}

export interface BlogComment {
  id: string;
  post_id: string;
  author_type: string;
  author_id: string;
  author_name: string;
  content: string;
  created_at: string;
}

export const REACTION_META: Record<string, { emoji: string; label: string }> = {
  like:       { emoji: "👍", label: "Like" },
  fire:       { emoji: "🔥", label: "Fire" },
  insightful: { emoji: "💡", label: "Insightful" },
  funny:      { emoji: "😄", label: "Funny" },
};

// ─────────────────────────────────────────────────────────────────────────────

// ── Hosted Agents ──────────────────────────────────────────────────────────

export interface ExternalAgentItem {
  id: string;
  name: string;
  handle: string;
  specialization: string;
  model_provider: string;
  model_name: string;
  is_active: boolean;
  is_hosted: false;
  karma: number;
  projects_created: number;
  github_connected: boolean;
  created_at: string | null;
  last_heartbeat: string | null;
}

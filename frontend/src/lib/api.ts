export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface PlatformStats {
  total_agents: number;
  active_agents: number;
  total_projects: number;
  total_code_commits: number;
  total_reviews: number;
  total_deploys: number;
  total_feature_requests: number;
  total_bug_reports: number;
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

export interface HackathonProject {
  id: string;
  title: string;
  description: string;
  score: number;
  wilson_score: number;
  votes_up: number;
  votes_down: number;
  agent_name: string;
  status: string;
  deploy_url: string | null;
  repo_url: string | null;
  team_id: string | null;
  team_name: string | null;
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

export interface Hackathon {
  id: string;
  title: string;
  theme: string;
  description: string;
  starts_at: string;
  ends_at: string;
  voting_ends_at: string;
  status: string;
  winner_project_id: string | null;
  prize_pool_usd: number;
  prize_description: string;
  created_at: string;
  projects?: HackathonProject[];
}

// ── Web3 / Ownership types ────────────────────────────────────────────────────

export interface ProjectTokenInfo {
  contract_address: string;
  chain_id: number;
  token_symbol: string | null;
  total_minted: number;
  basescan_url: string;
}

export interface ContributorShare {
  agent_id: string;
  agent_name: string;
  owner_user_id: string | null;
  owner_name: string | null;
  wallet_address: string | null;
  contribution_points: number;
  share_pct: number;
  tokens_minted: number;
  token_balance_onchain: number | null;
}

export interface ProjectOwnership {
  project_id: string;
  project_title: string;
  token: ProjectTokenInfo | null;
  contributors: ContributorShare[];
}

export interface UserTokenEntry {
  project_id: string;
  project_title: string;
  contract_address: string;
  token_symbol: string | null;
  token_balance: number;
  share_bps: number;
  basescan_url: string;
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

export interface ModelUsageStats {
  total_calls: number;
  unique_models: number;
  by_task: ModelUsageEntry[];
  by_model: ModelUsageEntry[];
}

export interface DirectMessage {
  id: string;
  from_name: string;
  from_handle: string | null;
  sender_type: "agent" | "human";
  content: string;
  is_read: boolean;
  created_at: string;
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

export const STATUS_COLORS: Record<string, { label: string; classes: string }> = {
  upcoming: { label: "Upcoming",    classes: "bg-neutral-700/50 text-neutral-400 border-neutral-600/30" },
  active:   { label: "Live",        classes: "bg-orange-400/15 text-orange-300 border-orange-400/20" },
  voting:   { label: "Voting",      classes: "bg-violet-400/15 text-violet-300 border-violet-400/20" },
  completed:{ label: "Completed",   classes: "bg-neutral-800/50 text-neutral-500 border-neutral-700/30" },
};

export function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
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

export function countdown(target: string): string {
  const diff = new Date(target).getTime() - Date.now();
  if (diff <= 0) return "Ended";
  const d = Math.floor(diff / 86400000);
  const h = Math.floor((diff % 86400000) / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  const s = Math.floor((diff % 60000) / 1000);
  return d > 0 ? `${d}d ${h}h ${m}m` : `${h}h ${m}m ${s}s`;
}

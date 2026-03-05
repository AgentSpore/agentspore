export interface AgentSporeConfig {
  apiKey: string;
  baseUrl?: string;
  timeout?: number;
}

export interface Agent {
  id: string;
  name: string;
  handle: string | null;
  api_key?: string;
  specialization: string;
  karma: number;
  projects_created: number;
  code_commits: number;
  reviews_done: number;
  is_active: boolean;
  created_at: string;
}

export interface Project {
  id: string;
  title: string;
  description: string;
  category: string;
  status: string;
  repo_url: string | null;
  deploy_url: string | null;
  tech_stack: string[];
  creator_agent_id: string;
}

export interface Task {
  id: string;
  type: string;
  title: string;
  description: string;
  priority: string;
  status: string;
  project_id: string | null;
  source_ref: string | null;
}

export interface HeartbeatResponse {
  tasks: Task[];
  notifications: Record<string, unknown>[];
  direct_messages: Record<string, unknown>[];
  feedback: Record<string, unknown>[];
}

export interface DirectMessage {
  id: string;
  from_name: string;
  content: string;
  is_read: boolean;
  created_at: string;
}

export interface Badge {
  badge_id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  rarity: string;
  awarded_at: string;
}

export interface CodeFile {
  path: string;
  content: string;
}

export type MessageType = "text" | "idea" | "question" | "alert";

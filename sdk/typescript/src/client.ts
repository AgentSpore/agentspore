import type {
  AgentSporeConfig, Badge, CodeFile, HeartbeatResponse, MessageType, Project, Task,
} from "./types.js";

class AgentSporeError extends Error {
  constructor(public statusCode: number, public detail: string) {
    super(`API error ${statusCode}: ${detail}`);
    this.name = "AgentSporeError";
  }
}

async function parseError(res: Response): Promise<AgentSporeError> {
  try {
    const data = await res.json();
    return new AgentSporeError(res.status, data.detail ?? res.statusText);
  } catch {
    return new AgentSporeError(res.status, res.statusText);
  }
}

export class AgentSpore {
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;

  constructor(config: AgentSporeConfig) {
    this.baseUrl = (config.baseUrl ?? "https://agentspore.com").replace(/\/$/, "");
    this.headers = {
      "Content-Type": "application/json",
      "X-API-Key": config.apiKey,
    };
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: this.headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw await parseError(res);
    return res.json() as Promise<T>;
  }

  // ── Registration ───────────────────────────────────────────────────────────

  static async register(params: {
    name: string;
    specialization: string;
    skills?: string[];
    modelProvider?: string;
    modelName?: string;
    description?: string;
    baseUrl?: string;
  }): Promise<AgentSpore> {
    const url = (params.baseUrl ?? "https://agentspore.com").replace(/\/$/, "");
    const res = await fetch(`${url}/api/v1/agents/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: params.name,
        specialization: params.specialization,
        skills: params.skills ?? [],
        model_provider: params.modelProvider ?? "openai",
        model_name: params.modelName ?? "gpt-4o",
        description: params.description ?? "",
      }),
    });
    if (!res.ok) throw await parseError(res);
    const data = await res.json() as { api_key: string };
    return new AgentSpore({ apiKey: data.api_key, baseUrl: url });
  }

  // ── Heartbeat ──────────────────────────────────────────────────────────────

  async heartbeat(params: {
    status?: string;
    capabilities?: string[];
    currentTask?: string | null;
    tasksCompleted?: number;
  } = {}): Promise<HeartbeatResponse> {
    const data = await this.request<{
      tasks: Task[];
      notifications: Record<string, unknown>[];
      direct_messages: Record<string, unknown>[];
      feedback: Record<string, unknown>[];
    }>("POST", "/api/v1/agents/heartbeat", {
      status: params.status ?? "idle",
      capabilities: params.capabilities ?? [],
      current_task: params.currentTask ?? null,
      tasks_completed: params.tasksCompleted ?? 0,
    });
    return data as HeartbeatResponse;
  }

  // ── Projects ───────────────────────────────────────────────────────────────

  async createProject(params: {
    title: string;
    description: string;
    category?: string;
    techStack?: string[];
    vcsProvider?: "github" | "gitlab";
  }): Promise<Project> {
    return this.request<Project>("POST", "/api/v1/agents/projects", {
      title: params.title,
      description: params.description,
      category: params.category ?? "other",
      tech_stack: params.techStack ?? [],
      vcs_provider: params.vcsProvider ?? "github",
    });
  }

  async pushCode(projectId: string, params: {
    files: CodeFile[];
    commitMessage: string;
    branch?: string;
  }): Promise<{ commit_sha: string; files_changed: number }> {
    return this.request("POST", `/api/v1/agents/projects/${projectId}/code`, {
      files: params.files,
      commit_message: params.commitMessage,
      branch: params.branch ?? "main",
    });
  }

  async reviewProject(projectId: string, params: {
    summary: string;
    comments: Array<{ file_path: string; line_number?: number; comment: string; suggestion?: string }>;
  }): Promise<Record<string, unknown>> {
    return this.request("POST", `/api/v1/agents/projects/${projectId}/review`, params);
  }

  // ── Chat ───────────────────────────────────────────────────────────────────

  async chat(content: string, type: MessageType = "text", projectId?: string): Promise<Record<string, unknown>> {
    return this.request("POST", "/api/v1/chat/message", {
      content,
      message_type: type,
      project_id: projectId ?? null,
    });
  }

  // ── Direct Messages ────────────────────────────────────────────────────────

  async replyDm(toHandle: string, content: string): Promise<Record<string, unknown>> {
    return this.request("POST", "/api/v1/chat/dm/reply", { to_handle: toHandle, content });
  }

  // ── Badges ─────────────────────────────────────────────────────────────────

  async getBadges(agentId: string): Promise<Badge[]> {
    return this.request<Badge[]>("GET", `/api/v1/agents/${agentId}/badges`);
  }

  // ── Profile ────────────────────────────────────────────────────────────────

  async me(): Promise<Record<string, unknown>> {
    return this.request("GET", "/api/v1/agents/me");
  }
}

export { AgentSporeError };

"""Agent API schemas — registration, heartbeat, projects, git, reviews, tasks, OAuth."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ── Agent registration & profile ──


class AgentRegisterRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=200)
    model_provider: str = Field(...)
    model_name: str = Field(...)
    specialization: str = Field(default="programmer")
    skills: list[str] = Field(default=[])
    description: str = Field(default="")
    dna_risk: int = Field(default=5, ge=1, le=10, description="1=safe/conservative, 10=bold/experimental")
    dna_speed: int = Field(default=5, ge=1, le=10, description="1=thorough/slow, 10=fast/ship-it")
    dna_verbosity: int = Field(default=5, ge=1, le=10, description="1=terse commits, 10=detailed docs")
    dna_creativity: int = Field(default=5, ge=1, le=10, description="1=conventional stack, 10=experimental tech")
    bio: str | None = Field(default=None, description="Self-written agent biography")
    owner_email: str = Field(..., description="Owner's email — used to auto-link agent to user account")


class AgentDNARequest(BaseModel):
    dna_risk: int | None = Field(default=None, ge=1, le=10)
    dna_speed: int | None = Field(default=None, ge=1, le=10)
    dna_verbosity: int | None = Field(default=None, ge=1, le=10)
    dna_creativity: int | None = Field(default=None, ge=1, le=10)
    bio: str | None = Field(default=None)


class AgentRegisterResponse(BaseModel):
    agent_id: str
    api_key: str
    name: str
    handle: str
    github_auth_url: str | None = None
    github_oauth_required: bool = False
    message: str = "Agent registered! Save your API key — it won't be shown again. Connect GitHub OAuth optionally for commit attribution."
    docs_url: str = "/skill.md"


class AgentProfile(BaseModel):
    id: str
    name: str
    handle: str = ""
    agent_type: str
    model_provider: str
    model_name: str
    specialization: str
    skills: list[str]
    karma: int
    projects_created: int
    code_commits: int
    reviews_done: int
    last_heartbeat: str | None
    is_active: bool
    created_at: str
    dna_risk: int = 5
    dna_speed: int = 5
    dna_verbosity: int = 5
    dna_creativity: int = 5
    bio: str | None = None


class GitHubActivityItem(BaseModel):
    id: str
    action_type: str
    description: str
    project_id: str | None = None
    project_title: str | None = None
    project_repo_url: str | None = None
    github_url: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    issue_number: int | None = None
    issue_title: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    issues_created: int | None = None
    commit_message: str | None = None
    fix_description: str | None = None
    dispute_reason: str | None = None
    created_at: str


# ── Heartbeat ──


class HeartbeatRequestBody(BaseModel):
    status: str = Field(default="idle")
    completed_tasks: list[dict[str, Any]] = Field(default=[])
    read_dm_ids: list[str] = Field(default=[])
    available_for: list[str] = Field(default=["programmer"])
    current_capacity: int = Field(default=3)


class HeartbeatResponseBody(BaseModel):
    tasks: list[dict[str, Any]] = []
    feedback: list[dict[str, Any]] = []
    notifications: list[dict[str, Any]] = []
    direct_messages: list[dict[str, Any]] = []
    rentals: list[dict[str, Any]] = []
    flow_steps: list[dict[str, Any]] = []
    mixer_chunks: list[dict[str, Any]] = []
    warnings: list[str] = []
    next_heartbeat_seconds: int = 14400


# ── Projects ──


class ProjectCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=300)
    description: str = Field(default="")
    category: str = Field(default="other")
    tech_stack: list[str] = Field(default=[])
    idea_id: str | None = Field(default=None)
    hackathon_id: UUID | None = Field(default=None)
    vcs_provider: str = Field(default="github", pattern="^(github|gitlab)$")


class ProjectResponse(BaseModel):
    id: str
    title: str
    description: str
    category: str
    creator_agent_id: str
    status: str
    votes_up: int
    votes_down: int
    tech_stack: list[str]
    deploy_url: str | None
    repo_url: str | None = None
    vcs_provider: str = "github"
    created_at: str


# ── Code & Git ──


class CodeSubmitRequest(BaseModel):
    files: list[dict[str, str]] = Field(...)
    commit_message: str = Field(default="Auto-commit by agent")
    branch: str = Field(default="main", description="Git branch to push to")


class BranchCreateRequest(BaseModel):
    branch_name: str = Field(..., min_length=1, max_length=200)
    from_branch: str = Field(default="main")


class PullRequestCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    body: str = Field(default="")
    head_branch: str = Field(...)
    base_branch: str = Field(default="main")


# ── Issues ──


class IssueCommentRequest(BaseModel):
    body: str = Field(..., min_length=1)
    on_behalf_of_agent_id: str | None = Field(
        default=None,
        description="If set, attribute the comment to this agent instead of the caller",
    )


class IssueCloseRequest(BaseModel):
    comment: str | None = Field(default=None, description="Optional explanation of the fix")
    on_behalf_of_agent_id: str | None = Field(
        default=None,
        description="If set, attribute the close action to this agent instead of the caller",
    )


# ── Code reviews ──


class ReviewCreateRequest(BaseModel):
    summary: str = Field(default="")
    status: str = Field(default="pending")
    comments: list[dict[str, Any]] = Field(default=[])
    model_used: str | None = Field(default=None, description="LLM model used for this review")


# ── Task Marketplace ──


class TaskClaimResponse(BaseModel):
    task_id: str
    status: str
    message: str


class TaskCompleteRequest(BaseModel):
    result: str = Field(default="", description="Summary of what was done")


# ── GitHub OAuth ──


class GitHubOAuthStatus(BaseModel):
    connected: bool
    github_login: str | None = None
    connected_at: str | None = None
    scopes: list[str] = []
    oauth_token: str | None = None


class GitHubOAuthCallbackResponse(BaseModel):
    status: str
    agent_id: str | None = None
    github_login: str | None = None
    message: str = ""


# ── GitLab OAuth ──


class GitLabOAuthStatus(BaseModel):
    connected: bool
    gitlab_login: str | None = None
    connected_at: str | None = None
    scopes: list[str] = []
    oauth_token: str | None = None


class GitLabOAuthCallbackResponse(BaseModel):
    status: str
    agent_id: str | None = None
    gitlab_login: str | None = None
    message: str = ""


# ── Platform stats ──


# ── Push Files ──


class PushFileItem(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    content: str | None = None
    delete: bool = False


class PushFilesRequest(BaseModel):
    files: list[PushFileItem] = Field(..., min_length=1)
    commit_message: str = Field(..., min_length=1, max_length=500)
    branch: str = "main"


# ── GitHub Proxy ──


class GitHubProxyRequest(BaseModel):
    method: str = Field(..., pattern="^(GET|POST|PATCH|PUT|DELETE)$")
    path: str = Field(..., min_length=1, max_length=1000, description="GitHub API path relative to /repos/{owner}/{repo}")
    body: dict[str, Any] | None = Field(default=None, description="Request body for POST/PATCH/PUT")


# ── Platform stats ──


class PlatformStats(BaseModel):
    total_agents: int
    active_agents: int
    total_projects: int
    total_code_commits: int
    total_reviews: int
    total_deploys: int
    total_feature_requests: int
    total_bug_reports: int

"""
Pydantic schemas — request/response models for AgentSpore API.
===============================================================
All schemas are organized by domain module.
"""

# Agent
from app.schemas.agents import (
    AgentDNARequest,
    AgentProfile,
    AgentRegisterRequest,
    AgentRegisterResponse,
    BranchCreateRequest,
    CodeSubmitRequest,
    GitHubActivityItem,
    GitHubOAuthCallbackResponse,
    GitHubOAuthStatus,
    GitLabOAuthCallbackResponse,
    GitLabOAuthStatus,
    HeartbeatRequestBody,
    HeartbeatResponseBody,
    IssueCloseRequest,
    IssueCommentRequest,
    PlatformStats,
    ProjectCreateRequest,
    ProjectResponse,
    PullRequestCreateRequest,
    ReviewCreateRequest,
    TaskClaimResponse,
    TaskCompleteRequest,
)

# Auth
from app.schemas.auth import (
    TokenRefresh,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)

# Analytics
from app.schemas.analytics import (
    ActivityPoint,
    LanguageStat,
    OverviewStats,
    TopAgent,
    TopProject,
)

# Badges
from app.schemas.badges import AgentBadge, BadgeDefinition

# Chat
from app.schemas.chat import AgentDMReply, ChatMessageRequest, DMRequest, HumanMessageRequest

# Discovery
from app.schemas.discovery import (
    GenerateIdeaRequest,
    GeneratedIdeaResponse,
    ProblemCreate,
    ProblemResponse,
    ProblemUpdate,
)

# Governance
from app.schemas.governance import AddContributorRequest, JoinRequest
from app.schemas.governance import VoteRequest as GovernanceVoteRequest

# Hackathons
from app.schemas.hackathons import (
    HackathonCreateRequest,
    HackathonDetailResponse,
    HackathonResponse,
    HackathonUpdateRequest,
)

# Ideas
from app.schemas.ideas import CommentCreate, CommentResponse, IdeaCreate, IdeaResponse, IdeaUpdate, IdeasListResponse
from app.schemas.ideas import VoteRequest as IdeaVoteRequest

# Ownership
from app.schemas.ownership import (
    ContributorShare,
    LinkOwnerRequest,
    ProjectOwnershipResponse,
    ProjectTokenInfo,
    UserTokenEntry,
    WalletConnectRequest,
)

# Projects
from app.schemas.projects import VoteRequest as ProjectVoteRequest

# Sandboxes
from app.schemas.sandboxes import (
    CodeGenerateResponse,
    CodeModifyRequest,
    CodeUpdateRequest,
    FeatureCreate,
    FeatureResponse,
    FeedbackCreate,
    FeedbackResponse,
    SandboxDetailResponse,
    SandboxPreviewResponse,
    SandboxResponse,
)

# Teams
from app.schemas.teams import (
    TeamCreateRequest,
    TeamMemberAddRequest,
    TeamMessageRequest,
    TeamProjectLinkRequest,
    TeamUpdateRequest,
)

# Tokens
from app.schemas.tokens import BalanceResponse, LeaderboardEntry, TransactionResponse

__all__ = [
    # agents
    "AgentRegisterRequest", "AgentDNARequest", "AgentRegisterResponse", "AgentProfile",
    "GitHubActivityItem", "HeartbeatRequestBody", "HeartbeatResponseBody",
    "ProjectCreateRequest", "ProjectResponse", "CodeSubmitRequest", "BranchCreateRequest",
    "PullRequestCreateRequest", "IssueCommentRequest", "IssueCloseRequest",
    "ReviewCreateRequest", "TaskClaimResponse", "TaskCompleteRequest",
    "GitHubOAuthStatus", "GitHubOAuthCallbackResponse", "GitLabOAuthStatus",
    "GitLabOAuthCallbackResponse", "PlatformStats",
    # auth
    "UserCreate", "UserLogin", "TokenResponse", "TokenRefresh", "UserResponse",
    # analytics
    "OverviewStats", "ActivityPoint", "TopAgent", "TopProject", "LanguageStat",
    # badges
    "BadgeDefinition", "AgentBadge",
    # chat
    "ChatMessageRequest", "HumanMessageRequest", "DMRequest", "AgentDMReply",
    # discovery
    "ProblemResponse", "ProblemCreate", "ProblemUpdate", "GenerateIdeaRequest", "GeneratedIdeaResponse",
    # governance
    "GovernanceVoteRequest", "AddContributorRequest", "JoinRequest",
    # hackathons
    "HackathonCreateRequest", "HackathonUpdateRequest", "HackathonResponse", "HackathonDetailResponse",
    # ideas
    "IdeaCreate", "IdeaUpdate", "IdeaResponse", "IdeaVoteRequest", "CommentCreate", "CommentResponse", "IdeasListResponse",
    # ownership
    "WalletConnectRequest", "LinkOwnerRequest", "ContributorShare", "ProjectTokenInfo",
    "ProjectOwnershipResponse", "UserTokenEntry",
    # projects
    "ProjectVoteRequest",
    # sandboxes
    "SandboxResponse", "SandboxDetailResponse", "FeedbackCreate", "FeedbackResponse",
    "FeatureCreate", "FeatureResponse", "CodeUpdateRequest", "CodeModifyRequest",
    "CodeGenerateResponse", "SandboxPreviewResponse",
    # teams
    "TeamCreateRequest", "TeamUpdateRequest", "TeamMemberAddRequest", "TeamMessageRequest", "TeamProjectLinkRequest",
    # tokens
    "BalanceResponse", "TransactionResponse", "LeaderboardEntry",
]

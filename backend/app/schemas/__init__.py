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
    # governance
    "GovernanceVoteRequest", "AddContributorRequest", "JoinRequest",
    # hackathons
    "HackathonCreateRequest", "HackathonUpdateRequest", "HackathonResponse", "HackathonDetailResponse",
    # ownership
    "WalletConnectRequest", "LinkOwnerRequest", "ContributorShare", "ProjectTokenInfo",
    "ProjectOwnershipResponse", "UserTokenEntry",
    # projects
    "ProjectVoteRequest",
    # teams
    "TeamCreateRequest", "TeamUpdateRequest", "TeamMemberAddRequest", "TeamMessageRequest", "TeamProjectLinkRequest",
    # tokens
    "BalanceResponse", "TransactionResponse", "LeaderboardEntry",
]

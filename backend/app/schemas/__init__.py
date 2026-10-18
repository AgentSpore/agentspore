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

# Badges
from app.schemas.badges import AgentBadge, BadgeDefinition

# Chat
from app.schemas.chat import AgentDMReply, ChatMessageRequest, DMRequest, HumanMessageRequest

# Governance
from app.schemas.governance import AddContributorRequest, JoinRequest
from app.schemas.governance import VoteRequest as GovernanceVoteRequest

# Ownership
from app.schemas.ownership import (
    ContributorShare,
    LinkOwnerRequest,
    ProjectOwnershipResponse,
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
    # badges
    "BadgeDefinition", "AgentBadge",
    # chat
    "ChatMessageRequest", "HumanMessageRequest", "DMRequest", "AgentDMReply",
    # governance
    "GovernanceVoteRequest", "AddContributorRequest", "JoinRequest",
    # ownership
    "WalletConnectRequest", "LinkOwnerRequest", "ContributorShare",
    "ProjectOwnershipResponse",
    # projects
    "ProjectVoteRequest",
    # teams
    "TeamCreateRequest", "TeamUpdateRequest", "TeamMemberAddRequest", "TeamMessageRequest", "TeamProjectLinkRequest",
    # tokens
    "BalanceResponse", "TransactionResponse", "LeaderboardEntry",
]

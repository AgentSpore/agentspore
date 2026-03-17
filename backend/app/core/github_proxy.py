"""GitHub Proxy configuration: allowed operations whitelist and karma rules."""

# Whitelist of allowed GitHub API operations.
# Patterns use * as a wildcard for path segments (e.g., /issues/* matches /issues/42).
# Anything not listed here is rejected with 403.
ALLOWED_OPERATIONS: dict[str, list[str]] = {
    "GET": [
        "/contents/*",           # read files (supports nested paths)
        "/git/trees/*",          # file tree
        "/issues",               # list issues
        "/issues/*",             # single issue
        "/issues/*/comments",    # issue comments
        "/pulls",                # list PRs
        "/pulls/*",              # single PR
        "/pulls/*/files",        # PR changed files
        "/pulls/*/comments",     # PR comments
        "/commits",              # list commits
        "/commits/*",            # single commit
        "/branches",             # list branches
        "/branches/*",           # single branch
        "/releases",             # list releases
        "/releases/*",           # single release
        "/readme",               # repo readme
    ],
    "POST": [
        "/issues",               # create issue
        "/issues/*/comments",    # comment on issue
        "/pulls",                # create PR
        "/pulls/*/comments",     # comment on PR
        "/releases",             # create release
        "/git/refs",             # create branch/tag
    ],
    "PUT": [
        "/contents",             # batch push (multiple files, atomic)
        "/contents/*",           # single file push (auto SHA)
    ],
    "PATCH": [
        "/issues/*",             # update/close issue
        "/pulls/*",              # update/close PR
        "/releases/*",           # update release
    ],
    "DELETE": [
        "/git/refs/*",           # delete branch/tag
        "/contents/*",           # delete file (auto SHA)
    ],
}

# Karma points awarded for write operations.
# Key: (method, path_pattern), Value: (action_name, karma_points).
# path_pattern uses * for any single segment.
KARMA_RULES: dict[tuple[str, str], tuple[str, int]] = {
    ("POST", "/issues"): ("issue_created", 5),
    ("POST", "/pulls"): ("pr_created", 10),
    ("POST", "/issues/*/comments"): ("issue_comment", 2),
    ("POST", "/pulls/*/comments"): ("pr_comment", 2),
    ("POST", "/releases"): ("release_created", 15),
    ("POST", "/git/refs"): ("branch_created", 3),
    ("PATCH", "/issues/*"): ("issue_updated", 2),
    ("PATCH", "/pulls/*"): ("pr_updated", 2),
    ("PUT", "/contents"): ("files_pushed", 10),
    ("PUT", "/contents/*"): ("file_pushed", 5),
    ("DELETE", "/contents/*"): ("file_deleted", 3),
}

# Rate limit: requests per hour per agent
RATE_LIMIT_PER_HOUR = 1000

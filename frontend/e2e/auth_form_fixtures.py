"""Exact synthetic auth contracts from the four form submit handlers."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

SOURCE = Path(__file__).resolve().parents[1] / "src/lib/i18n/account.ts"
MESSAGES = json.loads(
    SOURCE.read_text().split("export const accountMessages = ", 1)[1].split(" as const;", 1)[0]
)
EMAIL = "synthetic@example.invalid"
PASSWORD = "Synthetic-password-123"
TOKEN = "synthetic-reset"
ERROR = "Synthetic authentication rejected"
USER = {
    "id": "synthetic-owner",
    "email": EMAIL,
    "name": "Synthetic owner",
    "avatar_url": None,
    "token_balance": 0,
    "solana_wallet": None,
    "aspore_balance": 0,
    "is_admin": False,
    "created_at": "2026-10-19T09:00:00Z",
}
POST_KEYS = {
    "login": {"email", "password"},
    "register": {"email", "password", "name"},
    "forgot-password": {"email"},
    "reset-password": {"token", "new_password"},
}
PAGES = {
    "/signup": set(),
    "/login": {"tab", "next"},
    "/forgot-password": set(),
    "/reset-password": {"token"},
    "/profile": set(),
}
PREFETCH = set(PAGES) | set(
    "/ /workspace /projects /agents /showcase /dashboard /chat /teams /blog".split()
)
GETS = {
    "/api/v1/oauth/providers": {"github": True, "google": True},
    "/api/v1/auth/me": USER,
    "/api/v1/users/me/payouts": [],
    "/api/v1/users/me/aspore": {"aspore_balance": 0},
}


class AuthHTTP:
    """Hold only known auth writes; every other API request fails closed."""

    def __init__(self, base_url):
        self.origin = urlparse(base_url).netloc
        self.pending, self.requests, self.unexpected = [], [], []

    def reject(self, route):
        """Keep unknown requests visible without sending them to a live service."""
        request, target = route.request, urlparse(route.request.url)
        url = target.path + (f"?{target.query}" if target.netloc == self.origin else "")
        headers = {
            key: request.headers.get(key)
            for key in ("rsc", "next-router-prefetch", "purpose", "sec-purpose")
        }
        self.unexpected.append({"method": request.method, "url": url, "headers": headers})
        route.abort()

    def accepts_page(self, request, target):
        """Allow only known document query names, each occurring once."""
        query = parse_qs(target.query, keep_blank_values=True)
        allowed = PAGES.get(target.path)
        if (
            target.path in PREFETCH
            and request.headers.get("rsc") == "1"
            and request.headers.get("next-router-prefetch") == "1"
        ):
            allowed = PAGES.get(target.path, set()) | {"_rsc"}
        return (
            request.method == "GET"
            and allowed is not None
            and set(query) <= allowed
            and all(len(value) == 1 for value in query.values())
        )

    def route(self, route):
        """Match origin, path, method and query before accepting a synthetic request."""
        request, target = route.request, urlparse(route.request.url)
        if target.netloc != self.origin or target.scheme != "http":
            self.reject(route)
            return
        if request.method == "GET" and target.path.startswith("/_next/"):
            route.continue_()
            return
        if self.accepts_page(request, target):
            route.continue_()
            return
        if request.method == "GET" and not target.query and target.path in GETS:
            route.fulfill(json=GETS[target.path])
            return
        action = target.path.removeprefix("/api/v1/auth/")
        if request.method == "POST" and not target.query and action in POST_KEYS:
            body = request.post_data_json
            if isinstance(body, dict) and set(body) == POST_KEYS[action]:
                self.requests.append({"action": action, "body": body})
                self.pending.append(route)
                return
        if (
            request.method == "GET"
            and target.path == "/favicon.ico"
            and (not target.query or re.fullmatch(r"favicon\.[a-z0-9]{1,48}\.ico", target.query))
        ):
            route.fulfill(status=204)
            return
        self.reject(route)


class BrowserFixture:
    """Provide browser lifecycle for local synthetic HTTP suites."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=cls.options.executable)
        cls.options.screenshots.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()


def parse_options():
    """Require an explicit local stand and existing browser, avoiding live-service probes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--screenshots", type=Path, required=True)
    options, remaining = parser.parse_known_args()
    target = urlparse(options.base_url)
    if target.scheme != "http" or target.hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("--base-url must be an HTTP loopback URL")
    if (
        target.username
        or target.password
        or target.path not in ("", "/")
        or target.query
        or target.fragment
    ):
        parser.error("--base-url must contain only a loopback origin")
    options.base_url = options.base_url.rstrip("/")
    return options, remaining

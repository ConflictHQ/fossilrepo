"""GitHub REST API client for ticket and wiki sync.

Handles rate limiting (1 req/sec, exponential backoff on 403/429),
owner/repo parsing from git URLs, and Issue + Contents endpoints.
"""

import hashlib
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def parse_github_repo(git_url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub remote URL.

    Handles:
      https://github.com/owner/repo.git
      https://github.com/owner/repo
      git@github.com:owner/repo.git
    """
    patterns = [
        r"github\.com[/:]([^/]+)/([^/.]+?)(?:\.git)?$",
    ]
    for pat in patterns:
        m = re.search(pat, git_url)
        if m:
            return m.group(1), m.group(2)
    return None


class GitHubClient:
    """Rate-limited GitHub API client."""

    def __init__(self, token: str, min_interval: float = 1.0):
        self.token = token
        self.min_interval = min_interval
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def _request(self, method: str, path: str, max_retries: int = 3, **kwargs) -> requests.Response:
        """Make a rate-limited request with exponential backoff on 403/429."""
        url = f"{GITHUB_API}{path}" if path.startswith("/") else path

        for attempt in range(max_retries):
            self._throttle()
            self._last_request_at = time.monotonic()

            resp = self.session.request(method, url, timeout=30, **kwargs)

            if resp.status_code in (403, 429):
                retry_after = int(resp.headers.get("Retry-After", 0))
                wait = max(retry_after, 2 ** (attempt + 1))
                logger.warning("GitHub rate limited (%s), waiting %ds (attempt %d)", resp.status_code, wait, attempt + 1)
                time.sleep(wait)
                continue

            return resp

        return resp  # return last response even if still rate-limited

    def create_issue(self, owner: str, repo: str, title: str, body: str, state: str = "open") -> dict:
        """Create a GitHub issue. Returns {number, url, error}."""
        resp = self._request("POST", f"/repos/{owner}/{repo}/issues", json={"title": title, "body": body})

        if resp.status_code == 201:
            data = resp.json()
            result = {"number": data["number"], "url": data["html_url"], "error": ""}
            # Close if Fossil status maps to closed
            if state == "closed":
                self.update_issue(owner, repo, data["number"], state="closed")
            return result

        return {"number": 0, "url": "", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    def update_issue(self, owner: str, repo: str, issue_number: int, title: str = "", body: str = "", state: str = "") -> dict:
        """Update a GitHub issue. Returns {success, error}."""
        payload = {}
        if title:
            payload["title"] = title
        if body:
            payload["body"] = body
        if state:
            payload["state"] = state

        resp = self._request("PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", json=payload)

        if resp.status_code == 200:
            return {"success": True, "error": ""}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    def get_file_sha(self, owner: str, repo: str, path: str) -> str:
        """Get the SHA of an existing file (needed for updates). Returns '' if not found."""
        resp = self._request("GET", f"/repos/{owner}/{repo}/contents/{path}")
        if resp.status_code == 200:
            return resp.json().get("sha", "")
        return ""

    def create_or_update_file(self, owner: str, repo: str, path: str, content: str, message: str) -> dict:
        """Create or update a file via the GitHub Contents API. Returns {success, sha, error}."""
        import base64

        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        payload = {"message": message, "content": encoded}

        # Check if file exists to get its SHA (required for updates)
        existing_sha = self.get_file_sha(owner, repo, path)
        if existing_sha:
            payload["sha"] = existing_sha

        resp = self._request("PUT", f"/repos/{owner}/{repo}/contents/{path}", json=payload)

        if resp.status_code in (200, 201):
            data = resp.json()
            return {"success": True, "sha": data.get("content", {}).get("sha", ""), "error": ""}
        return {"success": False, "sha": "", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}


def fossil_status_to_github(fossil_status: str) -> str:
    """Map Fossil ticket status to GitHub issue state."""
    closed_statuses = {"closed", "fixed", "resolved", "wontfix", "unable_to_reproduce", "works_as_designed", "deferred"}
    return "closed" if fossil_status.lower().strip() in closed_statuses else "open"


def format_ticket_body(ticket, comments: list[dict] | None = None) -> str:
    """Format a Fossil ticket as GitHub issue body markdown."""
    parts = []

    if ticket.body:
        parts.append(ticket.body)

    # Metadata table
    meta = []
    if ticket.type:
        meta.append(f"| Type | {ticket.type} |")
    if ticket.priority:
        meta.append(f"| Priority | {ticket.priority} |")
    if ticket.severity:
        meta.append(f"| Severity | {ticket.severity} |")
    if ticket.subsystem:
        meta.append(f"| Subsystem | {ticket.subsystem} |")
    if ticket.resolution:
        meta.append(f"| Resolution | {ticket.resolution} |")
    if ticket.owner:
        meta.append(f"| Owner | {ticket.owner} |")

    if meta:
        parts.append("\n---\n**Fossil metadata**\n\n| Field | Value |\n|---|---|\n" + "\n".join(meta))

    if comments:
        parts.append("\n---\n**Comments**\n")
        for c in comments:
            ts = c["timestamp"].strftime("%Y-%m-%d %H:%M") if c.get("timestamp") else ""
            user = c.get("user", "")
            parts.append(f"**{user}** ({ts}):\n> {c['comment']}\n")

    parts.append(f"\n---\n*Synced from Fossil ticket `{ticket.uuid[:10]}`*")
    return "\n\n".join(parts)


def content_hash(text: str) -> str:
    """SHA-256 hash of content for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

"""Tests for fossil/github_api.py, fossil/oauth.py, and core/sanitize.py.

Covers:
- GitHubClient: rate limiting, issue CRUD, file CRUD, error handling
- parse_github_repo: URL format parsing
- fossil_status_to_github: status mapping
- format_ticket_body: markdown generation
- content_hash: deterministic hashing
- OAuth: authorize URL builders, token exchange (success + failure)
- Sanitize: edge cases not covered in test_security.py
"""

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from core.sanitize import (
    _is_safe_url,
    sanitize_html,
)
from fossil.github_api import (
    GitHubClient,
    content_hash,
    format_ticket_body,
    fossil_status_to_github,
    parse_github_repo,
)
from fossil.oauth import (
    GITHUB_AUTHORIZE_URL,
    GITLAB_AUTHORIZE_URL,
    github_authorize_url,
    github_exchange_token,
    gitlab_authorize_url,
    gitlab_exchange_token,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code=200, json_data=None, text="", headers=None):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.ok = 200 <= status_code < 300
    resp.headers = headers or {}
    return resp


# ===========================================================================
# fossil/github_api.py -- parse_github_repo
# ===========================================================================


class TestParseGithubRepo:
    def test_https_with_git_suffix(self):
        result = parse_github_repo("https://github.com/owner/repo.git")
        assert result == ("owner", "repo")

    def test_https_without_git_suffix(self):
        result = parse_github_repo("https://github.com/owner/repo")
        assert result == ("owner", "repo")

    def test_ssh_url(self):
        result = parse_github_repo("git@github.com:owner/repo.git")
        assert result == ("owner", "repo")

    def test_ssh_url_without_git_suffix(self):
        result = parse_github_repo("git@github.com:owner/repo")
        assert result == ("owner", "repo")

    def test_non_github_url_returns_none(self):
        assert parse_github_repo("https://gitlab.com/owner/repo.git") is None

    def test_malformed_url_returns_none(self):
        assert parse_github_repo("not-a-url") is None

    def test_empty_string_returns_none(self):
        assert parse_github_repo("") is None

    def test_owner_with_hyphens_and_dots(self):
        result = parse_github_repo("https://github.com/my-org.dev/my-repo.git")
        assert result == ("my-org.dev", "my-repo")

    def test_url_with_trailing_slash_returns_none(self):
        # The regex expects owner/repo at end of string, trailing slash breaks it
        assert parse_github_repo("https://github.com/owner/repo/") is None


# ===========================================================================
# fossil/github_api.py -- fossil_status_to_github
# ===========================================================================


class TestFossilStatusToGithub:
    @pytest.mark.parametrize(
        "status",
        ["closed", "fixed", "resolved", "wontfix", "unable_to_reproduce", "works_as_designed", "deferred"],
    )
    def test_closed_statuses(self, status):
        assert fossil_status_to_github(status) == "closed"

    @pytest.mark.parametrize("status", ["open", "active", "new", "review", "pending"])
    def test_open_statuses(self, status):
        assert fossil_status_to_github(status) == "open"

    def test_case_insensitive(self):
        assert fossil_status_to_github("CLOSED") == "closed"
        assert fossil_status_to_github("Fixed") == "closed"

    def test_strips_whitespace(self):
        assert fossil_status_to_github("  closed  ") == "closed"
        assert fossil_status_to_github(" open ") == "open"

    def test_empty_string_maps_to_open(self):
        assert fossil_status_to_github("") == "open"


# ===========================================================================
# fossil/github_api.py -- content_hash
# ===========================================================================


class TestContentHash:
    def test_deterministic(self):
        assert content_hash("hello") == content_hash("hello")

    def test_matches_sha256(self):
        expected = hashlib.sha256(b"hello").hexdigest()
        assert content_hash("hello") == expected

    def test_different_inputs_different_hashes(self):
        assert content_hash("hello") != content_hash("world")

    def test_empty_string(self):
        expected = hashlib.sha256(b"").hexdigest()
        assert content_hash("") == expected


# ===========================================================================
# fossil/github_api.py -- format_ticket_body
# ===========================================================================


class TestFormatTicketBody:
    def _ticket(self, **kwargs):
        defaults = {
            "body": "Bug description",
            "type": "bug",
            "priority": "high",
            "severity": "critical",
            "subsystem": "auth",
            "resolution": "",
            "owner": "alice",
            "uuid": "abcdef1234567890",
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_includes_body(self):
        ticket = self._ticket()
        result = format_ticket_body(ticket)
        assert "Bug description" in result

    def test_includes_metadata_table(self):
        ticket = self._ticket()
        result = format_ticket_body(ticket)
        assert "| Type | bug |" in result
        assert "| Priority | high |" in result
        assert "| Severity | critical |" in result
        assert "| Subsystem | auth |" in result
        assert "| Owner | alice |" in result

    def test_skips_empty_metadata_fields(self):
        ticket = self._ticket(type="", priority="", severity="", subsystem="", resolution="", owner="")
        result = format_ticket_body(ticket)
        assert "Fossil metadata" not in result

    def test_includes_uuid_trailer(self):
        ticket = self._ticket()
        result = format_ticket_body(ticket)
        assert "abcdef1234" in result

    def test_includes_comments(self):
        from datetime import datetime

        ticket = self._ticket()
        comments = [
            {"user": "bob", "timestamp": datetime(2025, 1, 15, 10, 30), "comment": "I can reproduce this."},
            {"user": "alice", "timestamp": datetime(2025, 1, 16, 14, 0), "comment": "Fix incoming."},
        ]
        result = format_ticket_body(ticket, comments=comments)
        assert "bob" in result
        assert "2025-01-15 10:30" in result
        assert "I can reproduce this." in result
        assert "alice" in result
        assert "Fix incoming." in result

    def test_no_comments(self):
        ticket = self._ticket()
        result = format_ticket_body(ticket, comments=None)
        assert "Comments" not in result

    def test_empty_comments_list(self):
        ticket = self._ticket()
        result = format_ticket_body(ticket, comments=[])
        assert "Comments" not in result

    def test_comment_without_timestamp(self):
        ticket = self._ticket()
        comments = [{"user": "dan", "comment": "No timestamp here."}]
        result = format_ticket_body(ticket, comments=comments)
        assert "dan" in result
        assert "No timestamp here." in result

    def test_resolution_shown_when_set(self):
        ticket = self._ticket(resolution="wontfix")
        result = format_ticket_body(ticket)
        assert "| Resolution | wontfix |" in result

    def test_no_body_ticket(self):
        ticket = self._ticket(body="")
        result = format_ticket_body(ticket)
        # Should still have the uuid trailer
        assert "abcdef1234" in result


# ===========================================================================
# fossil/github_api.py -- GitHubClient
# ===========================================================================


class TestGitHubClientInit:
    def test_session_headers(self):
        client = GitHubClient("ghp_test123", min_interval=0)
        assert client.session.headers["Authorization"] == "Bearer ghp_test123"
        assert "application/vnd.github+json" in client.session.headers["Accept"]
        assert client.session.headers["X-GitHub-Api-Version"] == "2022-11-28"


class TestGitHubClientRequest:
    """Tests for _request method: throttle, retry on 403/429."""

    def test_successful_request(self):
        client = GitHubClient("tok", min_interval=0)
        mock_resp = _mock_response(200, {"ok": True})

        with patch.object(client.session, "request", return_value=mock_resp):
            resp = client._request("GET", "/repos/owner/repo")
            assert resp.status_code == 200

    @patch("fossil.github_api.time.sleep")
    def test_retries_on_429(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        rate_limited = _mock_response(429, headers={"Retry-After": "1"})
        success = _mock_response(200, {"ok": True})

        with patch.object(client.session, "request", side_effect=[rate_limited, success]):
            resp = client._request("GET", "/repos/o/r", max_retries=3)
            assert resp.status_code == 200
            # Should have slept for the retry
            assert mock_sleep.call_count >= 1

    @patch("fossil.github_api.time.sleep")
    def test_retries_on_403(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        forbidden = _mock_response(403, headers={})
        success = _mock_response(200, {"ok": True})

        with patch.object(client.session, "request", side_effect=[forbidden, success]):
            resp = client._request("GET", "/repos/o/r", max_retries=3)
            assert resp.status_code == 200

    @patch("fossil.github_api.time.sleep")
    def test_exhausted_retries_returns_last_response(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        rate_limited = _mock_response(429, headers={})

        with patch.object(client.session, "request", return_value=rate_limited):
            resp = client._request("GET", "/repos/o/r", max_retries=2)
            assert resp.status_code == 429

    def test_absolute_url_not_prefixed(self):
        client = GitHubClient("tok", min_interval=0)
        mock_resp = _mock_response(200)

        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            client._request("GET", "https://custom.api.com/thing")
            # Should pass the absolute URL through unchanged
            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert call_args[0][1] == "https://custom.api.com/thing"


class TestGitHubClientCreateIssue:
    @patch("fossil.github_api.time.sleep")
    def test_create_issue_success(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        resp = _mock_response(201, {"number": 42, "html_url": "https://github.com/o/r/issues/42"})

        with patch.object(client.session, "request", return_value=resp):
            result = client.create_issue("o", "r", "Bug title", "Bug body")
            assert result["number"] == 42
            assert result["url"] == "https://github.com/o/r/issues/42"
            assert result["error"] == ""

    @patch("fossil.github_api.time.sleep")
    def test_create_issue_failure(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        resp = _mock_response(422, text="Validation Failed")

        with patch.object(client.session, "request", return_value=resp):
            result = client.create_issue("o", "r", "Bad", "data")
            assert result["number"] == 0
            assert result["url"] == ""
            assert "422" in result["error"]

    @patch("fossil.github_api.time.sleep")
    def test_create_issue_with_closed_state(self, mock_sleep):
        """Creating an issue with state='closed' should create then close it."""
        client = GitHubClient("tok", min_interval=0)
        create_resp = _mock_response(201, {"number": 99, "html_url": "https://github.com/o/r/issues/99"})
        close_resp = _mock_response(200, {"number": 99})

        with patch.object(client.session, "request", side_effect=[create_resp, close_resp]) as mock_req:
            result = client.create_issue("o", "r", "Fixed bug", "Already done", state="closed")
            assert result["number"] == 99
            # Should have made two requests: POST create + PATCH close
            assert mock_req.call_count == 2
            second_call = mock_req.call_args_list[1]
            assert second_call[0][0] == "PATCH"


class TestGitHubClientUpdateIssue:
    @patch("fossil.github_api.time.sleep")
    def test_update_issue_success(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        resp = _mock_response(200, {"number": 42})

        with patch.object(client.session, "request", return_value=resp):
            result = client.update_issue("o", "r", 42, title="New title", state="closed")
            assert result["success"] is True
            assert result["error"] == ""

    @patch("fossil.github_api.time.sleep")
    def test_update_issue_failure(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        resp = _mock_response(404, text="Not Found")

        with patch.object(client.session, "request", return_value=resp):
            result = client.update_issue("o", "r", 999, state="closed")
            assert result["success"] is False
            assert "404" in result["error"]

    @patch("fossil.github_api.time.sleep")
    def test_update_issue_builds_payload_selectively(self, mock_sleep):
        """Only non-empty fields should be in the payload."""
        client = GitHubClient("tok", min_interval=0)
        resp = _mock_response(200)

        with patch.object(client.session, "request", return_value=resp) as mock_req:
            client.update_issue("o", "r", 1, title="", body="new body", state="")
            call_kwargs = mock_req.call_args[1]
            payload = call_kwargs["json"]
            assert "title" not in payload
            assert "state" not in payload
            assert payload["body"] == "new body"


class TestGitHubClientGetFileSha:
    @patch("fossil.github_api.time.sleep")
    def test_get_file_sha_found(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        resp = _mock_response(200, {"sha": "abc123"})

        with patch.object(client.session, "request", return_value=resp):
            sha = client.get_file_sha("o", "r", "README.md")
            assert sha == "abc123"

    @patch("fossil.github_api.time.sleep")
    def test_get_file_sha_not_found(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        resp = _mock_response(404)

        with patch.object(client.session, "request", return_value=resp):
            sha = client.get_file_sha("o", "r", "nonexistent.md")
            assert sha == ""


class TestGitHubClientCreateOrUpdateFile:
    @patch("fossil.github_api.time.sleep")
    def test_create_new_file(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        get_resp = _mock_response(404)  # file does not exist
        put_resp = _mock_response(201, {"content": {"sha": "newsha"}})

        with patch.object(client.session, "request", side_effect=[get_resp, put_resp]) as mock_req:
            result = client.create_or_update_file("o", "r", "docs/new.md", "# New", "Add new doc")
            assert result["success"] is True
            assert result["sha"] == "newsha"
            assert result["error"] == ""
            # PUT payload should NOT have 'sha' key since file is new
            put_call = mock_req.call_args_list[1]
            payload = put_call[1]["json"]
            assert "sha" not in payload

    @patch("fossil.github_api.time.sleep")
    def test_update_existing_file(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        get_resp = _mock_response(200, {"sha": "oldsha"})  # file exists
        put_resp = _mock_response(200, {"content": {"sha": "updatedsha"}})

        with patch.object(client.session, "request", side_effect=[get_resp, put_resp]) as mock_req:
            result = client.create_or_update_file("o", "r", "docs/existing.md", "# Updated", "Update doc")
            assert result["success"] is True
            assert result["sha"] == "updatedsha"
            # PUT payload should include the existing SHA
            put_call = mock_req.call_args_list[1]
            payload = put_call[1]["json"]
            assert payload["sha"] == "oldsha"

    @patch("fossil.github_api.time.sleep")
    def test_create_or_update_file_failure(self, mock_sleep):
        client = GitHubClient("tok", min_interval=0)
        get_resp = _mock_response(404)
        put_resp = _mock_response(422, text="Validation Failed")

        with patch.object(client.session, "request", side_effect=[get_resp, put_resp]):
            result = client.create_or_update_file("o", "r", "bad.md", "content", "msg")
            assert result["success"] is False
            assert "422" in result["error"]

    @patch("fossil.github_api.time.sleep")
    def test_content_is_base64_encoded(self, mock_sleep):
        import base64

        client = GitHubClient("tok", min_interval=0)
        get_resp = _mock_response(404)
        put_resp = _mock_response(201, {"content": {"sha": "s"}})

        with patch.object(client.session, "request", side_effect=[get_resp, put_resp]) as mock_req:
            client.create_or_update_file("o", "r", "f.md", "hello world", "msg")
            put_call = mock_req.call_args_list[1]
            payload = put_call[1]["json"]
            decoded = base64.b64decode(payload["content"]).decode("utf-8")
            assert decoded == "hello world"


# ===========================================================================
# fossil/oauth.py -- authorize URL builders
# ===========================================================================


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def mock_session():
    """A dict-like session for request factory requests."""
    return {}


@pytest.mark.django_db
class TestGithubAuthorizeUrl:
    def test_returns_none_when_no_client_id(self, rf, mock_session):
        request = rf.get("/")
        request.session = mock_session
        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = ""

        with patch("constance.config", mock_config):
            url = github_authorize_url(request, "my-project")
            assert url is None

    def test_builds_url_with_all_params(self, rf, mock_session):
        request = rf.get("/")
        request.session = mock_session
        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = "client123"

        with patch("constance.config", mock_config):
            url = github_authorize_url(request, "my-proj", mirror_id="77")

        assert url.startswith(GITHUB_AUTHORIZE_URL)
        assert "client_id=client123" in url
        assert "scope=repo" in url
        assert "state=my-proj:77:" in url
        assert "redirect_uri=" in url
        assert "oauth_state_nonce" in mock_session

    def test_default_mirror_id_is_new(self, rf, mock_session):
        request = rf.get("/")
        request.session = mock_session
        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = "cid"

        with patch("constance.config", mock_config):
            url = github_authorize_url(request, "slug")

        assert ":new:" in url

    def test_nonce_stored_in_session(self, rf, mock_session):
        request = rf.get("/")
        request.session = mock_session
        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = "cid"

        with patch("constance.config", mock_config):
            github_authorize_url(request, "slug")

        nonce = mock_session["oauth_state_nonce"]
        assert len(nonce) > 20  # token_urlsafe(32) is ~43 chars


@pytest.mark.django_db
class TestGitlabAuthorizeUrl:
    def test_returns_none_when_no_client_id(self, rf, mock_session):
        request = rf.get("/")
        request.session = mock_session
        mock_config = MagicMock()
        mock_config.GITLAB_OAUTH_CLIENT_ID = ""

        with patch("constance.config", mock_config):
            url = gitlab_authorize_url(request, "proj")
            assert url is None

    def test_builds_url_with_all_params(self, rf, mock_session):
        request = rf.get("/")
        request.session = mock_session
        mock_config = MagicMock()
        mock_config.GITLAB_OAUTH_CLIENT_ID = "gl_client"

        with patch("constance.config", mock_config):
            url = gitlab_authorize_url(request, "proj", mirror_id="5")

        assert url.startswith(GITLAB_AUTHORIZE_URL)
        assert "client_id=gl_client" in url
        assert "response_type=code" in url
        assert "scope=api" in url
        assert "state=proj:5:" in url
        assert "oauth_state_nonce" in mock_session

    def test_default_mirror_id_is_new(self, rf, mock_session):
        request = rf.get("/")
        request.session = mock_session
        mock_config = MagicMock()
        mock_config.GITLAB_OAUTH_CLIENT_ID = "gl"

        with patch("constance.config", mock_config):
            url = gitlab_authorize_url(request, "slug")

        assert ":new:" in url


# ===========================================================================
# fossil/oauth.py -- token exchange
# ===========================================================================


@pytest.mark.django_db
class TestGithubExchangeToken:
    def test_returns_error_when_no_code(self, rf):
        request = rf.get("/callback/")  # no ?code= param
        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = "cid"
        mock_config.GITHUB_OAUTH_CLIENT_SECRET = "secret"

        with patch("constance.config", mock_config):
            result = github_exchange_token(request, "slug")

        assert result["error"] == "No code received"
        assert result["token"] == ""

    @patch("fossil.oauth.requests.get")
    @patch("fossil.oauth.requests.post")
    def test_successful_exchange(self, mock_post, mock_get, rf):
        request = rf.get("/callback/?code=authcode123")
        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = "cid"
        mock_config.GITHUB_OAUTH_CLIENT_SECRET = "secret"

        mock_post.return_value = _mock_response(200, {"access_token": "ghp_tok456"})
        mock_get.return_value = _mock_response(200, {"login": "octocat"})

        with patch("constance.config", mock_config):
            result = github_exchange_token(request, "slug")

        assert result["token"] == "ghp_tok456"
        assert result["username"] == "octocat"
        assert result["error"] == ""
        mock_post.assert_called_once()
        mock_get.assert_called_once()

    @patch("fossil.oauth.requests.post")
    def test_exchange_no_access_token_in_response(self, mock_post, rf):
        request = rf.get("/callback/?code=badcode")
        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = "cid"
        mock_config.GITHUB_OAUTH_CLIENT_SECRET = "secret"

        mock_post.return_value = _mock_response(200, {"error": "bad_verification_code", "error_description": "Bad code"})

        with patch("constance.config", mock_config):
            result = github_exchange_token(request, "slug")

        assert result["token"] == ""
        assert result["error"] == "Bad code"

    @patch("fossil.oauth.requests.post")
    def test_exchange_network_error(self, mock_post, rf):
        request = rf.get("/callback/?code=code")
        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = "cid"
        mock_config.GITHUB_OAUTH_CLIENT_SECRET = "secret"

        mock_post.side_effect = ConnectionError("Network unreachable")

        with patch("constance.config", mock_config):
            result = github_exchange_token(request, "slug")

        assert result["token"] == ""
        assert "Network unreachable" in result["error"]

    @patch("fossil.oauth.requests.get")
    @patch("fossil.oauth.requests.post")
    def test_exchange_user_api_fails(self, mock_post, mock_get, rf):
        """Token exchange succeeds but user info endpoint fails."""
        request = rf.get("/callback/?code=code")
        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = "cid"
        mock_config.GITHUB_OAUTH_CLIENT_SECRET = "secret"

        mock_post.return_value = _mock_response(200, {"access_token": "ghp_tok"})
        mock_get.return_value = _mock_response(401, {"message": "Bad credentials"})

        with patch("constance.config", mock_config):
            result = github_exchange_token(request, "slug")

        # Token should still be returned, username will be empty
        assert result["token"] == "ghp_tok"
        assert result["username"] == ""
        assert result["error"] == ""


@pytest.mark.django_db
class TestGitlabExchangeToken:
    def test_returns_error_when_no_code(self, rf):
        request = rf.get("/callback/")
        mock_config = MagicMock()
        mock_config.GITLAB_OAUTH_CLIENT_ID = "cid"
        mock_config.GITLAB_OAUTH_CLIENT_SECRET = "secret"

        with patch("constance.config", mock_config):
            result = gitlab_exchange_token(request, "slug")

        assert result["error"] == "No code received"
        assert result["token"] == ""

    @patch("fossil.oauth.requests.post")
    def test_successful_exchange(self, mock_post, rf):
        request = rf.get("/callback/?code=glcode")
        mock_config = MagicMock()
        mock_config.GITLAB_OAUTH_CLIENT_ID = "cid"
        mock_config.GITLAB_OAUTH_CLIENT_SECRET = "secret"

        mock_post.return_value = _mock_response(200, {"access_token": "glpat_token789"})

        with patch("constance.config", mock_config):
            result = gitlab_exchange_token(request, "slug")

        assert result["token"] == "glpat_token789"
        assert result["error"] == ""

    @patch("fossil.oauth.requests.post")
    def test_exchange_no_access_token(self, mock_post, rf):
        request = rf.get("/callback/?code=badcode")
        mock_config = MagicMock()
        mock_config.GITLAB_OAUTH_CLIENT_ID = "cid"
        mock_config.GITLAB_OAUTH_CLIENT_SECRET = "secret"

        mock_post.return_value = _mock_response(200, {"error_description": "Invalid code"})

        with patch("constance.config", mock_config):
            result = gitlab_exchange_token(request, "slug")

        assert result["token"] == ""
        assert result["error"] == "Invalid code"

    @patch("fossil.oauth.requests.post")
    def test_exchange_network_error(self, mock_post, rf):
        request = rf.get("/callback/?code=code")
        mock_config = MagicMock()
        mock_config.GITLAB_OAUTH_CLIENT_ID = "cid"
        mock_config.GITLAB_OAUTH_CLIENT_SECRET = "secret"

        mock_post.side_effect = TimeoutError("Connection timed out")

        with patch("constance.config", mock_config):
            result = gitlab_exchange_token(request, "slug")

        assert result["token"] == ""
        assert "timed out" in result["error"]

    @patch("fossil.oauth.requests.post")
    def test_exchange_sends_correct_payload(self, mock_post, rf):
        """Verify the POST body includes grant_type and redirect_uri for GitLab."""
        request = rf.get("/callback/?code=code")
        mock_config = MagicMock()
        mock_config.GITLAB_OAUTH_CLIENT_ID = "gl_cid"
        mock_config.GITLAB_OAUTH_CLIENT_SECRET = "gl_secret"

        mock_post.return_value = _mock_response(200, {"access_token": "tok"})

        with patch("constance.config", mock_config):
            gitlab_exchange_token(request, "slug")

        call_kwargs = mock_post.call_args[1]
        data = call_kwargs["data"]
        assert data["grant_type"] == "authorization_code"
        assert data["client_id"] == "gl_cid"
        assert data["client_secret"] == "gl_secret"
        assert data["code"] == "code"
        assert "/oauth/callback/gitlab/" in data["redirect_uri"]


# ===========================================================================
# core/sanitize.py -- edge cases not in test_security.py
# ===========================================================================


class TestSanitizeAllowedTags:
    """Verify specific allowed tags survive sanitization."""

    @pytest.mark.parametrize(
        "tag",
        ["abbr", "acronym", "dd", "del", "details", "dl", "dt", "ins", "kbd", "mark", "q", "s", "samp", "small", "sub", "sup", "tt", "var"],
    )
    def test_inline_tags_preserved(self, tag):
        html_in = f"<{tag}>content</{tag}>"
        result = sanitize_html(html_in)
        assert f"<{tag}>" in result
        assert f"</{tag}>" in result

    def test_summary_tag_preserved(self):
        html_in = '<details open class="info"><summary class="title">Details</summary>Content</details>'
        result = sanitize_html(html_in)
        assert "<details" in result
        assert "<summary" in result
        assert "Details" in result


class TestSanitizeAttributeFiltering:
    """Verify attribute allowlist/blocklist behavior."""

    def test_strips_non_allowed_attributes(self):
        html_in = '<p style="color:red" data-custom="x">text</p>'
        result = sanitize_html(html_in)
        assert "style=" not in result
        assert "data-custom=" not in result
        assert "<p>" in result

    def test_table_colspan_preserved(self):
        html_in = '<table><tr><td colspan="2" class="wide">cell</td></tr></table>'
        result = sanitize_html(html_in)
        assert 'colspan="2"' in result

    def test_ol_start_and_type_preserved(self):
        html_in = '<ol start="5" type="a"><li>item</li></ol>'
        result = sanitize_html(html_in)
        assert 'start="5"' in result
        assert 'type="a"' in result

    def test_li_value_preserved(self):
        html_in = '<ul><li value="3">item</li></ul>'
        result = sanitize_html(html_in)
        assert 'value="3"' in result

    def test_heading_id_preserved(self):
        html_in = '<h2 id="section-1" class="title">Title</h2>'
        result = sanitize_html(html_in)
        assert 'id="section-1"' in result
        assert 'class="title"' in result

    def test_a_name_attribute_preserved(self):
        html_in = '<a name="anchor">anchor</a>'
        result = sanitize_html(html_in)
        assert 'name="anchor"' in result

    def test_boolean_attribute_no_value(self):
        html_in = "<details open><summary>info</summary>body</details>"
        result = sanitize_html(html_in)
        assert "<details open>" in result


class TestSanitizeUrlSchemes:
    """Test URL protocol validation in href/src attributes."""

    def test_http_allowed(self):
        assert _is_safe_url("http://example.com") is True

    def test_https_allowed(self):
        assert _is_safe_url("https://example.com") is True

    def test_mailto_allowed(self):
        assert _is_safe_url("mailto:user@example.com") is True

    def test_ftp_allowed(self):
        assert _is_safe_url("ftp://files.example.com/doc.txt") is True

    def test_javascript_blocked(self):
        assert _is_safe_url("javascript:alert(1)") is False

    def test_vbscript_blocked(self):
        assert _is_safe_url("vbscript:MsgBox") is False

    def test_data_blocked(self):
        assert _is_safe_url("data:text/html,<script>alert(1)</script>") is False

    def test_entity_encoded_javascript_blocked(self):
        """HTML entity encoding should not bypass protocol check."""
        assert _is_safe_url("&#106;avascript:alert(1)") is False

    def test_tab_in_protocol_blocked(self):
        """Tabs injected in the protocol name should be stripped before checking."""
        assert _is_safe_url("jav\tascript:alert(1)") is False

    def test_cr_in_protocol_blocked(self):
        assert _is_safe_url("java\rscript:alert(1)") is False

    def test_newline_in_protocol_blocked(self):
        assert _is_safe_url("java\nscript:alert(1)") is False

    def test_null_byte_in_protocol_blocked(self):
        assert _is_safe_url("java\x00script:alert(1)") is False

    def test_fragment_only_allowed(self):
        assert _is_safe_url("#section") is True

    def test_relative_url_allowed(self):
        assert _is_safe_url("/page/about") is True

    def test_empty_url_allowed(self):
        assert _is_safe_url("") is True

    def test_mixed_case_protocol_blocked(self):
        assert _is_safe_url("JaVaScRiPt:alert(1)") is False


class TestSanitizeHrefSrcReplacement:
    """Verify that unsafe URLs in href/src are replaced with '#'."""

    def test_javascript_href_neutralized(self):
        html_in = '<a href="javascript:alert(1)">link</a>'
        result = sanitize_html(html_in)
        assert 'href="#"' in result
        assert "javascript" not in result

    def test_data_src_neutralized(self):
        html_in = '<img src="data:image/svg+xml,<script>alert(1)</script>">'
        result = sanitize_html(html_in)
        assert 'src="#"' in result

    def test_safe_href_preserved(self):
        html_in = '<a href="https://example.com">link</a>'
        result = sanitize_html(html_in)
        assert 'href="https://example.com"' in result


class TestSanitizeDangerousTags:
    """Test the container vs void dangerous tag distinction."""

    def test_script_content_fully_removed(self):
        html_in = "<p>before</p><script>var x = 1;</script><p>after</p>"
        result = sanitize_html(html_in)
        assert "var x" not in result
        assert "<p>before</p>" in result
        assert "<p>after</p>" in result

    def test_style_content_fully_removed(self):
        html_in = "<div>ok</div><style>.evil { display:none }</style><div>fine</div>"
        result = sanitize_html(html_in)
        assert ".evil" not in result
        assert "<div>ok</div>" in result

    def test_iframe_content_fully_removed(self):
        html_in = '<iframe src="x">text inside iframe</iframe>'
        result = sanitize_html(html_in)
        assert "text inside iframe" not in result
        assert "<iframe" not in result

    def test_nested_dangerous_tags(self):
        """Nested script tags should be fully stripped."""
        html_in = "<script><script>inner</script></script><p>safe</p>"
        result = sanitize_html(html_in)
        assert "inner" not in result
        assert "<p>safe</p>" in result

    def test_base_tag_stripped(self):
        html_in = '<base href="https://evil.com/">'
        result = sanitize_html(html_in)
        assert "<base" not in result

    def test_meta_tag_stripped(self):
        html_in = '<meta http-equiv="refresh" content="0;url=https://evil.com">'
        result = sanitize_html(html_in)
        assert "<meta" not in result

    def test_link_tag_stripped(self):
        html_in = '<link rel="stylesheet" href="https://evil.com/style.css">'
        result = sanitize_html(html_in)
        assert "<link" not in result


class TestSanitizeTextPreservation:
    """Verify text inside stripped tags is preserved vs. removed appropriately."""

    def test_unknown_tag_text_preserved(self):
        """Unknown non-dangerous tags are stripped but their text content remains."""
        html_in = "<custom>inner text</custom>"
        result = sanitize_html(html_in)
        assert "<custom>" not in result
        assert "inner text" in result

    def test_form_content_fully_removed(self):
        """Form is a dangerous container -- content inside should be dropped."""
        html_in = "<form>login prompt</form>"
        result = sanitize_html(html_in)
        assert "login prompt" not in result

    def test_object_content_fully_removed(self):
        html_in = "<object>fallback text</object>"
        result = sanitize_html(html_in)
        assert "fallback text" not in result

    def test_embed_is_dangerous_container(self):
        html_in = "<embed>text</embed>"
        result = sanitize_html(html_in)
        assert "text" not in result


class TestSanitizeEntityHandling:
    """Verify HTML entity passthrough outside dangerous contexts."""

    def test_named_entity_preserved(self):
        html_in = "<p>&amp; &lt; &gt;</p>"
        result = sanitize_html(html_in)
        assert "&amp;" in result
        assert "&lt;" in result
        assert "&gt;" in result

    def test_numeric_entity_preserved(self):
        html_in = "<p>&#169; &#8212;</p>"
        result = sanitize_html(html_in)
        assert "&#169;" in result
        assert "&#8212;" in result

    def test_entities_inside_script_stripped(self):
        html_in = "<script>&amp; entity</script>"
        result = sanitize_html(html_in)
        assert "&amp;" not in result


class TestSanitizeComments:
    def test_html_comments_stripped(self):
        html_in = "<p>before</p><!-- secret comment --><p>after</p>"
        result = sanitize_html(html_in)
        assert "secret comment" not in result
        assert "<!--" not in result
        assert "<p>before</p>" in result
        assert "<p>after</p>" in result

    def test_conditional_comment_stripped(self):
        html_in = "<!--[if IE]>evil<![endif]--><p>safe</p>"
        result = sanitize_html(html_in)
        assert "evil" not in result
        assert "<p>safe</p>" in result


class TestSanitizeSVG:
    """SVG support for Pikchr diagrams."""

    def test_svg_with_allowed_attrs(self):
        html_in = (
            '<svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg"><rect x="10" y="10" width="80" height="80" fill="blue"/></svg>'
        )
        result = sanitize_html(html_in)
        assert "<svg" in result
        assert "<rect" in result
        assert 'fill="blue"' in result

    def test_svg_strips_script_inside(self):
        html_in = '<svg><script>alert(1)</script><circle cx="50" cy="50" r="40"/></svg>'
        result = sanitize_html(html_in)
        assert "<script" not in result
        assert "alert" not in result
        assert "<circle" in result

    def test_svg_strips_event_handler(self):
        html_in = '<svg onload="alert(1)"><circle cx="50" cy="50" r="40"/></svg>'
        result = sanitize_html(html_in)
        assert "onload" not in result
        assert "<circle" in result

    def test_svg_path_preserved(self):
        html_in = '<svg><path d="M10 10 L90 90" stroke="black" stroke-width="2"/></svg>'
        result = sanitize_html(html_in)
        assert "<path" in result
        assert 'stroke="black"' in result

    def test_svg_text_element(self):
        html_in = '<svg><text x="10" y="20" font-size="14" fill="black">Label</text></svg>'
        result = sanitize_html(html_in)
        assert "<text" in result
        assert "Label" in result

    def test_svg_g_transform(self):
        html_in = '<svg><g transform="translate(10,20)"><circle cx="0" cy="0" r="5"/></g></svg>'
        result = sanitize_html(html_in)
        assert "<g" in result
        assert 'transform="translate(10,20)"' in result


class TestSanitizeAttributeEscaping:
    """Verify attribute values are properly escaped in output."""

    def test_ampersand_in_href_escaped(self):
        html_in = '<a href="https://example.com?a=1&b=2">link</a>'
        result = sanitize_html(html_in)
        assert "&amp;" in result

    def test_quote_in_attribute_escaped(self):
        html_in = '<a href="https://example.com" title="a &quot;quoted&quot; title">link</a>'
        result = sanitize_html(html_in)
        assert "&quot;" in result or "&#34;" in result


class TestSanitizeSelfClosingTags:
    """Handle self-closing (void) tags."""

    def test_br_self_closing(self):
        html_in = "line1<br/>line2"
        result = sanitize_html(html_in)
        assert "<br>" in result
        assert "line1" in result
        assert "line2" in result

    def test_img_self_closing_with_attrs(self):
        html_in = '<img src="photo.jpg" alt="A photo"/>'
        result = sanitize_html(html_in)
        assert 'src="photo.jpg"' in result
        assert 'alt="A photo"' in result

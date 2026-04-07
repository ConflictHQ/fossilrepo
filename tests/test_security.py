"""Security regression tests for SSH key injection, stored XSS, forum IDOR, OAuth CSRF, Git mirror token exposure, and open redirect."""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory

from core.sanitize import sanitize_html
from fossil.forum import ForumPost
from fossil.models import FossilRepository
from organization.models import Team
from projects.models import Project, ProjectTeam

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_client(db):
    User.objects.create_user(username="sectest", password="testpass123")
    client = Client()
    client.login(username="sectest", password="testpass123")
    return client


@pytest.fixture
def request_factory():
    return RequestFactory()


# ---------------------------------------------------------------------------
# 4. OAuth CSRF / Session Poisoning
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOAuthNonceGeneration:
    """Authorize URL builders must store a nonce in the session and embed it in state."""

    def test_github_authorize_url_includes_nonce(self, authed_client, request_factory):
        import re

        from fossil.oauth import github_authorize_url

        request = request_factory.get("/")
        request.session = authed_client.session

        mock_config = MagicMock()
        mock_config.GITHUB_OAUTH_CLIENT_ID = "test-client-id"
        with patch("constance.config", mock_config):
            url = github_authorize_url(request, "my-project", mirror_id="42")

        assert url is not None
        # State param should have three colon-separated parts: slug:mirror_id:nonce
        m = re.search(r"state=([^&]+)", url)
        assert m is not None
        parts = m.group(1).split(":")
        assert len(parts) == 3
        assert parts[0] == "my-project"
        assert parts[1] == "42"
        # Nonce was stored in session
        assert request.session["oauth_state_nonce"] == parts[2]
        assert len(parts[2]) > 20  # token_urlsafe(32) produces ~43 chars

    def test_gitlab_authorize_url_includes_nonce(self, authed_client, request_factory):
        import re

        from fossil.oauth import gitlab_authorize_url

        request = request_factory.get("/")
        request.session = authed_client.session

        mock_config = MagicMock()
        mock_config.GITLAB_OAUTH_CLIENT_ID = "test-client-id"
        with patch("constance.config", mock_config):
            url = gitlab_authorize_url(request, "my-project")

        assert url is not None
        m = re.search(r"state=([^&]+)", url)
        assert m is not None
        parts = m.group(1).split(":")
        assert len(parts) == 3
        assert parts[0] == "my-project"
        assert parts[1] == "new"
        assert request.session["oauth_state_nonce"] == parts[2]


@pytest.mark.django_db
class TestOAuthCallbackNonceValidation:
    """Callback handlers must reject requests with missing or mismatched nonce."""

    def test_github_callback_rejects_missing_nonce(self, authed_client):
        """State with only slug:mirror_id (no nonce) is rejected."""
        response = authed_client.get("/oauth/callback/github/", {"state": "my-project:new", "code": "abc123"})
        # Should redirect to dashboard since state has < 3 parts
        assert response.status_code == 302
        assert response.url == "/dashboard/"

    def test_github_callback_rejects_wrong_nonce(self, authed_client):
        """A forged nonce that doesn't match the session is rejected."""
        session = authed_client.session
        session["oauth_state_nonce"] = "real-nonce-value"
        session.save()

        response = authed_client.get(
            "/oauth/callback/github/",
            {"state": "my-project:new:forged-nonce", "code": "abc123"},
        )
        assert response.status_code == 302
        assert "/projects/my-project/fossil/sync/git/" in response.url

    def test_github_callback_rejects_empty_nonce_in_state(self, authed_client):
        """State with an empty nonce segment is rejected even if session has no nonce."""
        response = authed_client.get(
            "/oauth/callback/github/",
            {"state": "my-project:new:", "code": "abc123"},
        )
        assert response.status_code == 302
        assert "/projects/my-project/fossil/sync/git/" in response.url

    @patch("fossil.oauth.github_exchange_token")
    def test_github_callback_accepts_valid_nonce(self, mock_exchange, authed_client):
        """A correct nonce passes validation and proceeds to token exchange."""
        mock_exchange.return_value = {"token": "ghp_fake", "username": "testuser", "error": ""}

        session = authed_client.session
        session["oauth_state_nonce"] = "correct-nonce"
        session.save()

        response = authed_client.get(
            "/oauth/callback/github/",
            {"state": "my-project:new:correct-nonce", "code": "abc123"},
        )
        assert response.status_code == 302
        assert "/projects/my-project/fossil/sync/git/" in response.url
        mock_exchange.assert_called_once()

        # Nonce consumed from session (popped)
        session = authed_client.session
        assert "oauth_state_nonce" not in session

    def test_gitlab_callback_rejects_missing_nonce(self, authed_client):
        response = authed_client.get("/oauth/callback/gitlab/", {"state": "my-project:new", "code": "abc123"})
        assert response.status_code == 302
        assert response.url == "/dashboard/"

    def test_gitlab_callback_rejects_wrong_nonce(self, authed_client):
        session = authed_client.session
        session["oauth_state_nonce"] = "real-nonce"
        session.save()

        response = authed_client.get(
            "/oauth/callback/gitlab/",
            {"state": "my-project:new:forged-nonce", "code": "abc123"},
        )
        assert response.status_code == 302
        assert "/projects/my-project/fossil/sync/git/" in response.url

    @patch("fossil.oauth.gitlab_exchange_token")
    def test_gitlab_callback_accepts_valid_nonce(self, mock_exchange, authed_client):
        mock_exchange.return_value = {"token": "glpat_fake", "error": ""}

        session = authed_client.session
        session["oauth_state_nonce"] = "correct-nonce"
        session.save()

        response = authed_client.get(
            "/oauth/callback/gitlab/",
            {"state": "my-project:new:correct-nonce", "code": "abc123"},
        )
        assert response.status_code == 302
        assert "/projects/my-project/fossil/sync/git/" in response.url
        mock_exchange.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Git Mirror Secret Exposure
# ---------------------------------------------------------------------------


class TestGitExportTokenHandling:
    """git_export must never embed tokens in command args or leak them in output."""

    def test_token_not_in_command_args(self):
        """When auth_token is provided, it must not appear in the subprocess command."""
        from fossil.cli import FossilCLI

        cli = FossilCLI(binary="/usr/bin/false")
        captured_cmd = []

        def capture_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("subprocess.run", side_effect=capture_run):
            cli.git_export(
                repo_path=MagicMock(__str__=lambda s: "/tmp/test.fossil"),
                mirror_dir=MagicMock(**{"mkdir.return_value": None}),
                autopush_url="https://github.com/user/repo.git",
                auth_token="ghp_s3cretTOKEN123",
            )

        cmd_str = " ".join(captured_cmd)
        assert "ghp_s3cretTOKEN123" not in cmd_str
        # URL should not have token embedded
        assert "ghp_s3cretTOKEN123@" not in cmd_str

    def test_token_passed_via_env(self):
        """When auth_token is provided, git credential helper is configured via env."""
        from fossil.cli import FossilCLI

        cli = FossilCLI(binary="/usr/bin/false")
        captured_env = {}

        def capture_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("subprocess.run", side_effect=capture_run):
            cli.git_export(
                repo_path=MagicMock(__str__=lambda s: "/tmp/test.fossil"),
                mirror_dir=MagicMock(**{"mkdir.return_value": None}),
                autopush_url="https://github.com/user/repo.git",
                auth_token="ghp_s3cretTOKEN123",
            )

        assert captured_env.get("GIT_TERMINAL_PROMPT") == "0"
        assert captured_env.get("GIT_CONFIG_COUNT") == "1"
        assert captured_env.get("GIT_CONFIG_KEY_0") == "credential.helper"
        assert "ghp_s3cretTOKEN123" in captured_env.get("GIT_CONFIG_VALUE_0", "")

    def test_token_redacted_from_output(self):
        """If the token somehow leaks into Fossil/Git stdout, it is scrubbed."""
        from fossil.cli import FossilCLI

        cli = FossilCLI(binary="/usr/bin/false")

        def fake_run(cmd, **kwargs):
            return MagicMock(
                returncode=0,
                stdout="push https://ghp_s3cretTOKEN123@github.com/user/repo.git",
                stderr="",
            )

        with patch("subprocess.run", side_effect=fake_run):
            result = cli.git_export(
                repo_path=MagicMock(__str__=lambda s: "/tmp/test.fossil"),
                mirror_dir=MagicMock(**{"mkdir.return_value": None}),
                autopush_url="https://github.com/user/repo.git",
                auth_token="ghp_s3cretTOKEN123",
            )

        assert "ghp_s3cretTOKEN123" not in result["message"]
        assert "[REDACTED]" in result["message"]

    def test_no_env_auth_when_no_token(self):
        """Without auth_token, no credential helper env vars are set."""
        from fossil.cli import FossilCLI

        cli = FossilCLI(binary="/usr/bin/false")
        captured_env = {}

        def capture_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("subprocess.run", side_effect=capture_run):
            cli.git_export(
                repo_path=MagicMock(__str__=lambda s: "/tmp/test.fossil"),
                mirror_dir=MagicMock(**{"mkdir.return_value": None}),
                autopush_url="https://github.com/user/repo.git",
                auth_token="",
            )

        assert "GIT_CONFIG_COUNT" not in captured_env
        assert "GIT_TERMINAL_PROMPT" not in captured_env


@pytest.mark.django_db
class TestGitSyncTaskTokenScrubbing:
    """run_git_sync must never embed tokens in URLs or persist them in logs."""

    def test_sync_task_does_not_embed_token_in_url(self, sample_project, admin_user, tmp_path):
        from fossil.models import FossilRepository
        from fossil.sync_models import GitMirror

        repo = FossilRepository.objects.get(project=sample_project)

        mirror = GitMirror.objects.create(
            repository=repo,
            git_remote_url="https://github.com/user/repo.git",
            auth_method="token",
            auth_credential="ghp_SECRETTOKEN",
            sync_mode="scheduled",
            created_by=admin_user,
        )

        captured_kwargs = {}

        def fake_git_export(repo_path, mirror_dir, autopush_url="", auth_token=""):
            captured_kwargs["autopush_url"] = autopush_url
            captured_kwargs["auth_token"] = auth_token
            return {"success": True, "message": "ok"}

        mock_config = MagicMock()
        mock_config.GIT_MIRROR_DIR = str(tmp_path / "mirrors")

        with (
            patch("fossil.cli.FossilCLI.is_available", return_value=True),
            patch("fossil.cli.FossilCLI.ensure_default_user"),
            patch("fossil.cli.FossilCLI.git_export", side_effect=fake_git_export),
            patch("fossil.models.FossilRepository.exists_on_disk", new_callable=lambda: property(lambda self: True)),
            patch("constance.config", mock_config),
        ):
            from fossil.tasks import run_git_sync

            run_git_sync(mirror_id=mirror.pk)

        # URL must not contain the token
        assert "ghp_SECRETTOKEN" not in captured_kwargs["autopush_url"]
        assert captured_kwargs["autopush_url"] == "https://github.com/user/repo.git"
        # Token passed separately
        assert captured_kwargs["auth_token"] == "ghp_SECRETTOKEN"

    def test_sync_task_scrubs_token_from_log(self, sample_project, admin_user, tmp_path):
        from fossil.models import FossilRepository
        from fossil.sync_models import GitMirror, SyncLog

        repo = FossilRepository.objects.get(project=sample_project)

        mirror = GitMirror.objects.create(
            repository=repo,
            git_remote_url="https://github.com/user/repo.git",
            auth_method="token",
            auth_credential="ghp_LEAKYTOKEN",
            sync_mode="scheduled",
            created_by=admin_user,
        )

        def fake_git_export(repo_path, mirror_dir, autopush_url="", auth_token=""):
            # Simulate output that accidentally includes the token
            return {"success": True, "message": "push https://ghp_LEAKYTOKEN@github.com/user/repo.git main"}

        mock_config = MagicMock()
        mock_config.GIT_MIRROR_DIR = str(tmp_path / "mirrors")

        with (
            patch("fossil.cli.FossilCLI.is_available", return_value=True),
            patch("fossil.cli.FossilCLI.ensure_default_user"),
            patch("fossil.cli.FossilCLI.git_export", side_effect=fake_git_export),
            patch("fossil.models.FossilRepository.exists_on_disk", new_callable=lambda: property(lambda self: True)),
            patch("constance.config", mock_config),
        ):
            from fossil.tasks import run_git_sync

            run_git_sync(mirror_id=mirror.pk)

        log = SyncLog.objects.filter(mirror=mirror).first()
        assert log is not None
        assert "ghp_LEAKYTOKEN" not in log.message
        assert "[REDACTED]" in log.message

        mirror.refresh_from_db()
        assert "ghp_LEAKYTOKEN" not in mirror.last_sync_message
        assert "[REDACTED]" in mirror.last_sync_message


# ---------------------------------------------------------------------------
# 6. Open Redirect on Login
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLoginOpenRedirect:
    """Login view must reject external URLs in the 'next' parameter."""

    def test_login_redirects_to_dashboard_by_default(self, db):
        User.objects.create_user(username="logintest", password="testpass123")
        client = Client()
        response = client.post("/auth/login/", {"username": "logintest", "password": "testpass123"})
        assert response.status_code == 302
        assert response.url == "/dashboard/"

    def test_login_respects_safe_next_url(self, db):
        User.objects.create_user(username="logintest", password="testpass123")
        client = Client()
        response = client.post("/auth/login/?next=/projects/", {"username": "logintest", "password": "testpass123"})
        assert response.status_code == 302
        assert response.url == "/projects/"

    def test_login_rejects_external_next_url(self, db):
        """An absolute URL pointing to an external host must be ignored."""
        User.objects.create_user(username="logintest", password="testpass123")
        client = Client()
        response = client.post(
            "/auth/login/?next=https://evil.example.com/steal",
            {"username": "logintest", "password": "testpass123"},
        )
        assert response.status_code == 302
        # Must NOT redirect to the external URL
        assert "evil.example.com" not in response.url
        assert response.url == "/dashboard/"

    def test_login_rejects_protocol_relative_url(self, db):
        """Protocol-relative URLs (//evil.com/path) are also external redirects."""
        User.objects.create_user(username="logintest", password="testpass123")
        client = Client()
        response = client.post(
            "/auth/login/?next=//evil.example.com/steal",
            {"username": "logintest", "password": "testpass123"},
        )
        assert response.status_code == 302
        assert "evil.example.com" not in response.url
        assert response.url == "/dashboard/"

    def test_login_rejects_javascript_url(self, db):
        User.objects.create_user(username="logintest", password="testpass123")
        client = Client()
        response = client.post(
            "/auth/login/?next=javascript:alert(1)",
            {"username": "logintest", "password": "testpass123"},
        )
        assert response.status_code == 302
        assert "javascript" not in response.url
        assert response.url == "/dashboard/"

    def test_login_with_empty_next_goes_to_dashboard(self, db):
        User.objects.create_user(username="logintest", password="testpass123")
        client = Client()
        response = client.post("/auth/login/?next=", {"username": "logintest", "password": "testpass123"})
        assert response.status_code == 302
        assert response.url == "/dashboard/"


# ===========================================================================
# 7. SSH Key Injection
# ===========================================================================


@pytest.fixture
def second_project(db, org, admin_user):
    """A second project for cross-project IDOR tests."""
    return Project.objects.create(
        name="Backend API",
        organization=org,
        visibility="private",
        created_by=admin_user,
    )


@pytest.fixture
def second_fossil_repo(db, second_project):
    """FossilRepository for the second project."""
    return FossilRepository.objects.get(project=second_project, deleted_at__isnull=True)


@pytest.fixture
def writer_sec_user(db, admin_user, sample_project):
    """User with write access to sample_project only."""
    writer = User.objects.create_user(username="writer_sec", password="testpass123")
    team = Team.objects.create(name="Writers Sec", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_sec_client(writer_sec_user):
    client = Client()
    client.login(username="writer_sec", password="testpass123")
    return client


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def other_project_thread(second_fossil_repo, admin_user):
    """A forum thread belonging to the second (different) project."""
    post = ForumPost.objects.create(
        repository=second_fossil_repo,
        title="Other Project Thread",
        body="This belongs to a different project.",
        created_by=admin_user,
    )
    post.thread_root = post
    post.save(update_fields=["thread_root", "updated_at", "version"])
    return post


@pytest.mark.django_db
class TestSSHKeyInjection:
    """Verify that SSH key uploads reject injection payloads."""

    def test_rejects_newline_injection(self, admin_client):
        """A key with embedded newlines could inject a second authorized_keys entry."""
        payload = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey test@host\nssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAttacker attacker"
        response = admin_client.post(
            "/auth/ssh-keys/",
            {"title": "injected", "public_key": payload},
        )
        # Should stay on the form (200), not redirect (302)
        assert response.status_code == 200
        content = response.content.decode()
        assert "single line" in content.lower() or "Newlines" in content

    def test_rejects_carriage_return_injection(self, admin_client):
        payload = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey test\rssh-rsa AAAA attacker"
        response = admin_client.post(
            "/auth/ssh-keys/",
            {"title": "cr-inject", "public_key": payload},
        )
        assert response.status_code == 200

    def test_rejects_null_byte_injection(self, admin_client):
        payload = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey test\x00ssh-rsa AAAA attacker"
        response = admin_client.post(
            "/auth/ssh-keys/",
            {"title": "null-inject", "public_key": payload},
        )
        assert response.status_code == 200

    def test_rejects_unknown_key_type(self, admin_client):
        response = admin_client.post(
            "/auth/ssh-keys/",
            {"title": "bad-type", "public_key": "ssh-fake AAAAC3NzaC test"},
        )
        assert response.status_code == 200
        assert "Unsupported key type" in response.content.decode()

    def test_rejects_too_many_parts(self, admin_client):
        response = admin_client.post(
            "/auth/ssh-keys/",
            {"title": "too-many", "public_key": "ssh-ed25519 AAAA comment extra-field"},
        )
        assert response.status_code == 200
        assert "Invalid SSH key format" in response.content.decode()

    def test_rejects_single_part(self, admin_client):
        response = admin_client.post(
            "/auth/ssh-keys/",
            {"title": "one-part", "public_key": "ssh-ed25519"},
        )
        assert response.status_code == 200
        assert "Invalid SSH key format" in response.content.decode()

    @patch("accounts.views._regenerate_authorized_keys")
    def test_accepts_valid_ed25519_key(self, mock_regen, admin_client):
        valid_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGNfRWJ2MjY3dTAwMjMyNDgyNjkzODQ3 user@host"
        response = admin_client.post(
            "/auth/ssh-keys/",
            {"title": "good-key", "public_key": valid_key},
        )
        # Should redirect on success
        assert response.status_code == 302
        mock_regen.assert_called_once()

    @patch("accounts.views._regenerate_authorized_keys")
    def test_accepts_valid_rsa_key_no_comment(self, mock_regen, admin_client):
        valid_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQ=="
        response = admin_client.post(
            "/auth/ssh-keys/",
            {"title": "rsa-key", "public_key": valid_key},
        )
        assert response.status_code == 302

    @patch("accounts.views._regenerate_authorized_keys")
    def test_accepts_ecdsa_key(self, mock_regen, admin_client):
        valid_key = "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTY= host"
        response = admin_client.post(
            "/auth/ssh-keys/",
            {"title": "ecdsa-key", "public_key": valid_key},
        )
        assert response.status_code == 302


# ===========================================================================
# 8. Stored XSS / HTML Sanitization
# ===========================================================================


@pytest.mark.django_db
class TestHTMLSanitization:
    """Verify that sanitize_html strips dangerous content."""

    def test_strips_script_tags(self):
        html = '<p>Hello</p><script>alert("xss")</script><p>World</p>'
        result = sanitize_html(html)
        assert "<script>" not in result
        assert "alert" not in result
        assert "<p>Hello</p>" in result
        assert "<p>World</p>" in result

    def test_strips_script_self_closing(self):
        html = '<p>Hi</p><script src="evil.js"/>'
        result = sanitize_html(html)
        assert "<script" not in result

    def test_strips_style_tags(self):
        html = "<div>Content</div><style>body{display:none}</style>"
        result = sanitize_html(html)
        assert "<style>" not in result
        assert "display:none" not in result
        assert "<div>Content</div>" in result

    def test_strips_iframe(self):
        html = '<iframe src="https://evil.com"></iframe>'
        result = sanitize_html(html)
        assert "<iframe" not in result

    def test_strips_object_embed(self):
        html = '<object data="evil.swf"></object><embed src="evil.swf">'
        result = sanitize_html(html)
        assert "<object" not in result
        assert "<embed" not in result

    def test_strips_event_handlers(self):
        html = '<img src="photo.jpg" onerror="alert(1)" alt="pic">'
        result = sanitize_html(html)
        assert "onerror" not in result
        assert "alert" not in result
        # The tag itself and safe attributes should survive
        assert "photo.jpg" in result
        assert 'alt="pic"' in result

    def test_strips_onclick(self):
        html = '<a href="/page" onclick="steal()">Click</a>'
        result = sanitize_html(html)
        assert "onclick" not in result
        assert "steal" not in result
        assert 'href="/page"' in result

    def test_neutralizes_javascript_url(self):
        html = '<a href="javascript:alert(1)">link</a>'
        result = sanitize_html(html)
        assert "javascript:" not in result

    def test_neutralizes_data_url(self):
        html = '<a href="data:text/html,<script>alert(1)</script>">link</a>'
        result = sanitize_html(html)
        assert "data:" not in result

    def test_neutralizes_javascript_src(self):
        html = '<img src="javascript:alert(1)">'
        result = sanitize_html(html)
        assert "javascript:" not in result

    def test_preserves_safe_html(self):
        safe = '<h1 id="title">Hello</h1><p>Text with <strong>bold</strong> and <a href="/page">link</a></p>'
        result = sanitize_html(safe)
        assert result == safe

    def test_preserves_svg_for_pikchr(self):
        svg = '<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="red"/></svg>'
        result = sanitize_html(svg)
        assert "<svg" in result
        assert "<circle" in result

    def test_strips_form_tags(self):
        html = '<form action="/steal"><input name="token"><button>Submit</button></form>'
        result = sanitize_html(html)
        assert "<form" not in result

    def test_handles_empty_string(self):
        assert sanitize_html("") == ""

    def test_handles_none_passthrough(self):
        assert sanitize_html(None) is None

    def test_case_insensitive_script_strip(self):
        html = "<SCRIPT>alert(1)</SCRIPT>"
        result = sanitize_html(html)
        assert "alert" not in result

    def test_strips_base_tag(self):
        """<base> can redirect all relative URLs to an attacker domain."""
        html = '<base href="https://evil.com/"><a href="/page">link</a>'
        result = sanitize_html(html)
        assert "<base" not in result
        assert 'href="/page"' in result


@pytest.mark.django_db
class TestXSSInPageView:
    """Verify XSS payloads are sanitized when rendered through the pages app."""

    def test_script_stripped_from_page_content(self, admin_client, org, admin_user):
        from pages.models import Page

        page = Page.objects.create(
            name="XSS Test Page",
            content='# Hello\n\n<script>alert("xss")</script>\n\nSafe text.',
            organization=org,
            created_by=admin_user,
        )
        response = admin_client.get(f"/kb/{page.slug}/")
        assert response.status_code == 200
        body = response.content.decode()
        # The base template has legitimate <script> tags (Tailwind, Alpine, theme).
        # Check that the *injected* XSS payload is stripped, not template scripts.
        assert 'alert("xss")' not in body
        assert "Safe text" in body


# ===========================================================================
# 9. Forum Thread IDOR
# ===========================================================================


@pytest.mark.django_db
class TestForumIDOR:
    """Verify that forum operations are scoped to the correct project's repository."""

    def test_cannot_view_thread_from_another_project(self, admin_client, sample_project, other_project_thread):
        """Accessing project A's forum with project B's thread ID should 404."""
        response = admin_client.get(
            f"/projects/{sample_project.slug}/fossil/forum/{other_project_thread.pk}/",
        )
        # The thread belongs to second_project, not sample_project.
        # Before the fix this returned 200; after the fix it falls through
        # to the Fossil-native lookup which also won't find it -> 404.
        assert response.status_code == 404

    def test_cannot_reply_to_thread_from_another_project(self, admin_client, sample_project, other_project_thread):
        """Replying via project A's URL to a thread in project B should 404."""
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/forum/{other_project_thread.pk}/reply/",
            {"body": "Injected cross-project reply"},
        )
        assert response.status_code == 404
        # Verify no reply was actually created
        assert ForumPost.objects.filter(parent=other_project_thread).count() == 0

    def test_can_view_own_project_thread(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Sanity check: a thread in the correct project should work fine."""
        thread = ForumPost.objects.create(
            repository=fossil_repo_obj,
            title="Valid Thread",
            body="This thread belongs here.",
            created_by=admin_user,
        )
        thread.thread_root = thread
        thread.save(update_fields=["thread_root", "updated_at", "version"])

        response = admin_client.get(
            f"/projects/{sample_project.slug}/fossil/forum/{thread.pk}/",
        )
        assert response.status_code == 200
        assert "Valid Thread" in response.content.decode()

    def test_can_reply_to_own_project_thread(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Sanity check: replying to a thread in the correct project works."""
        thread = ForumPost.objects.create(
            repository=fossil_repo_obj,
            title="Reply Target",
            body="Thread body.",
            created_by=admin_user,
        )
        thread.thread_root = thread
        thread.save(update_fields=["thread_root", "updated_at", "version"])

        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/forum/{thread.pk}/reply/",
            {"body": "Valid reply"},
        )
        assert response.status_code == 302
        assert ForumPost.objects.filter(parent=thread).count() == 1

    def test_forum_list_only_shows_own_project_threads(
        self, admin_client, sample_project, fossil_repo_obj, other_project_thread, admin_user
    ):
        """Forum list for project A should not include project B's threads."""
        own_thread = ForumPost.objects.create(
            repository=fossil_repo_obj,
            title="Project A Thread",
            body="This is in project A.",
            created_by=admin_user,
        )
        own_thread.thread_root = own_thread
        own_thread.save(update_fields=["thread_root", "updated_at", "version"])

        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/forum/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Project A Thread" in body
        assert "Other Project Thread" not in body

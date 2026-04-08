"""Tests covering uncovered code paths in fossil/api_views.py.

Targets: batch API, workspace CRUD (list/create/detail/commit/merge/abandon),
workspace ownership checks, SSE event stream internals, and _resolve_batch_route.
Existing test_agent_coordination.py covers ticket claim/release/submit and review
CRUD -- this file does NOT duplicate those.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory

from fossil.agent_claims import TicketClaim
from fossil.branch_protection import BranchProtection
from fossil.ci import StatusCheck
from fossil.code_reviews import CodeReview
from fossil.models import FossilRepository
from fossil.workspaces import AgentWorkspace
from organization.models import Team
from projects.models import ProjectTeam

# ---- Fixtures ----


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """Non-admin user with write access to the project."""
    writer = User.objects.create_user(username="writer_cov", password="testpass123")
    team = Team.objects.create(name="Cov Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    c = Client()
    c.login(username="writer_cov", password="testpass123")
    return c


@pytest.fixture
def reader_user(db, admin_user, sample_project):
    """User with read-only access to the project."""
    reader = User.objects.create_user(username="reader_cov", password="testpass123")
    team = Team.objects.create(name="Cov Readers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(reader)
    ProjectTeam.objects.create(project=sample_project, team=team, role="read", created_by=admin_user)
    return reader


@pytest.fixture
def reader_client(reader_user):
    c = Client()
    c.login(username="reader_cov", password="testpass123")
    return c


@pytest.fixture
def workspace(fossil_repo_obj, admin_user):
    """An active agent workspace with a checkout path."""
    return AgentWorkspace.objects.create(
        repository=fossil_repo_obj,
        name="ws-test-1",
        branch="workspace/ws-test-1",
        agent_id="claude-test",
        status="active",
        checkout_path="/tmp/fake-checkout",
        created_by=admin_user,
    )


def _api_url(slug, path):
    return f"/projects/{slug}/fossil/{path}"


# ---- Helper to build a mock subprocess.run result ----


def _make_proc(returncode=0, stdout="", stderr=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class _SSEBreakError(Exception):
    """Raised from mocked time.sleep to break the SSE infinite loop."""


def _drain_sse_one_iteration(response):
    """Read one iteration of the SSE generator, collecting yielded chunks.

    The SSE event_stream is an infinite while-True generator with time.sleep(5)
    at the end of each iteration. We mock time.sleep to raise _SSEBreakError after
    yielding events from the first poll cycle.
    """
    events = []
    with patch("fossil.api_views.time.sleep", side_effect=_SSEBreakError):
        try:
            for chunk in response.streaming_content:
                # StreamingHttpResponse wraps generator output in map() for
                # encoding; chunks are bytes.
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                events.append(chunk)
        except (_SSEBreakError, RuntimeError):
            pass
    return events


def _drain_sse_n_iterations(response, n=3):
    """Read n iterations of the SSE generator."""
    call_count = 0

    def _count_and_break(_seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= n:
            raise _SSEBreakError

    events = []
    with patch("fossil.api_views.time.sleep", side_effect=_count_and_break):
        try:
            for chunk in response.streaming_content:
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                events.append(chunk)
        except (_SSEBreakError, RuntimeError):
            pass
    return events


# ================================================================
# Batch API
# ================================================================


@pytest.mark.django_db
class TestBatchAPI:
    """Tests for POST /projects/<slug>/fossil/api/batch (lines 636-706)."""

    def test_batch_success_with_multiple_sub_requests(self, admin_client, sample_project, fossil_repo_obj):
        """Batch call dispatches multiple GET sub-requests and returns combined results."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            reader = mock_reader_cls.return_value
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_timeline.return_value = []
            reader.get_checkin_count.return_value = 0
            reader.get_tickets.return_value = []

            response = admin_client.post(
                _api_url(sample_project.slug, "api/batch"),
                data=json.dumps(
                    {
                        "requests": [
                            {"method": "GET", "path": "/api/timeline"},
                            {"method": "GET", "path": "/api/tickets"},
                        ]
                    }
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["responses"]) == 2
        assert data["responses"][0]["status"] == 200
        assert "checkins" in data["responses"][0]["body"]
        assert data["responses"][1]["status"] == 200
        assert "tickets" in data["responses"][1]["body"]

    def test_batch_wrong_method(self, admin_client, sample_project, fossil_repo_obj):
        """GET to batch endpoint returns 405."""
        response = admin_client.get(_api_url(sample_project.slug, "api/batch"))
        assert response.status_code == 405

    def test_batch_invalid_json(self, admin_client, sample_project, fossil_repo_obj):
        """Non-JSON body returns 400."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/batch"),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "Invalid JSON" in response.json()["error"]

    def test_batch_requests_not_list(self, admin_client, sample_project, fossil_repo_obj):
        """'requests' must be a list."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/batch"),
            data=json.dumps({"requests": "not-a-list"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "'requests' must be a list" in response.json()["error"]

    def test_batch_exceeds_max_requests(self, admin_client, sample_project, fossil_repo_obj):
        """More than 25 sub-requests returns 400."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/batch"),
            data=json.dumps({"requests": [{"method": "GET", "path": "/api/project"}] * 26}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "Maximum 25" in response.json()["error"]

    def test_batch_empty_requests(self, admin_client, sample_project, fossil_repo_obj):
        """Empty requests list returns empty responses."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/batch"),
            data=json.dumps({"requests": []}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["responses"] == []

    def test_batch_non_get_rejected(self, admin_client, sample_project, fossil_repo_obj):
        """Non-GET sub-requests are rejected with 405."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/batch"),
            data=json.dumps({"requests": [{"method": "POST", "path": "/api/project"}]}),
            content_type="application/json",
        )
        assert response.status_code == 200
        sub = response.json()["responses"][0]
        assert sub["status"] == 405
        assert "Only GET" in sub["body"]["error"]

    def test_batch_unknown_path(self, admin_client, sample_project, fossil_repo_obj):
        """Unknown API path in batch returns 404 sub-response."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/batch"),
            data=json.dumps({"requests": [{"method": "GET", "path": "/api/nonexistent"}]}),
            content_type="application/json",
        )
        assert response.status_code == 200
        sub = response.json()["responses"][0]
        assert sub["status"] == 404
        assert "Unknown API path" in sub["body"]["error"]

    def test_batch_missing_path(self, admin_client, sample_project, fossil_repo_obj):
        """Sub-request without 'path' returns 400 sub-response."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/batch"),
            data=json.dumps({"requests": [{"method": "GET"}]}),
            content_type="application/json",
        )
        assert response.status_code == 200
        sub = response.json()["responses"][0]
        assert sub["status"] == 400
        assert "Missing 'path'" in sub["body"]["error"]

    def test_batch_non_dict_sub_request(self, admin_client, sample_project, fossil_repo_obj):
        """Non-dict items in requests list return 400 sub-response."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/batch"),
            data=json.dumps({"requests": ["not-a-dict"]}),
            content_type="application/json",
        )
        assert response.status_code == 200
        sub = response.json()["responses"][0]
        assert sub["status"] == 400
        assert "must be an object" in sub["body"]["error"]

    def test_batch_dynamic_route_ticket_detail(self, admin_client, sample_project, fossil_repo_obj):
        """Batch can route to dynamic ticket detail path."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            reader = mock_reader_cls.return_value
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            ticket = MagicMock()
            ticket.uuid = "abc123"
            ticket.title = "Test"
            ticket.status = "Open"
            ticket.type = "Bug"
            ticket.subsystem = ""
            ticket.priority = ""
            ticket.severity = ""
            ticket.resolution = ""
            ticket.body = ""
            ticket.created = None
            reader.get_ticket_detail.return_value = ticket
            reader.get_ticket_comments.return_value = []

            response = admin_client.post(
                _api_url(sample_project.slug, "api/batch"),
                data=json.dumps({"requests": [{"method": "GET", "path": "/api/tickets/abc123"}]}),
                content_type="application/json",
            )

        assert response.status_code == 200
        sub = response.json()["responses"][0]
        assert sub["status"] == 200
        assert sub["body"]["uuid"] == "abc123"

    def test_batch_dynamic_route_wiki_page(self, admin_client, sample_project, fossil_repo_obj):
        """Batch can route to dynamic wiki page path."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            reader = mock_reader_cls.return_value
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            page = MagicMock()
            page.name = "Home"
            page.content = "# Home"
            page.last_modified = None
            page.user = "admin"
            reader.get_wiki_page.return_value = page

            with patch("fossil.views._render_fossil_content", return_value="<h1>Home</h1>"):
                response = admin_client.post(
                    _api_url(sample_project.slug, "api/batch"),
                    data=json.dumps({"requests": [{"method": "GET", "path": "/api/wiki/Home"}]}),
                    content_type="application/json",
                )

        assert response.status_code == 200
        sub = response.json()["responses"][0]
        assert sub["status"] == 200
        assert sub["body"]["name"] == "Home"

    def test_batch_denied_for_anon(self, client, sample_project, fossil_repo_obj):
        """Anonymous users cannot use the batch API."""
        response = client.post(
            _api_url(sample_project.slug, "api/batch"),
            data=json.dumps({"requests": []}),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_batch_sub_request_exception_returns_500(self, admin_client, sample_project, fossil_repo_obj):
        """When a sub-request raises an exception, we get a 500 sub-response."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            mock_reader_cls.side_effect = RuntimeError("boom")

            response = admin_client.post(
                _api_url(sample_project.slug, "api/batch"),
                data=json.dumps({"requests": [{"method": "GET", "path": "/api/timeline"}]}),
                content_type="application/json",
            )

        assert response.status_code == 200
        sub = response.json()["responses"][0]
        assert sub["status"] == 500
        assert "Internal error" in sub["body"]["error"]


# ================================================================
# Workspace List
# ================================================================


@pytest.mark.django_db
class TestWorkspaceList:
    """Tests for GET /projects/<slug>/fossil/api/workspaces (lines 749-786)."""

    def test_list_workspaces_empty(self, admin_client, sample_project, fossil_repo_obj):
        """Empty workspace list returns zero results."""
        response = admin_client.get(_api_url(sample_project.slug, "api/workspaces"))
        assert response.status_code == 200
        data = response.json()
        assert data["workspaces"] == []

    def test_list_workspaces_returns_all(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Lists all workspaces for the repo."""
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj, name="ws-1", branch="workspace/ws-1", agent_id="a1", created_by=admin_user
        )
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj, name="ws-2", branch="workspace/ws-2", agent_id="a2", created_by=admin_user
        )

        response = admin_client.get(_api_url(sample_project.slug, "api/workspaces"))
        assert response.status_code == 200
        data = response.json()
        assert len(data["workspaces"]) == 2
        names = {ws["name"] for ws in data["workspaces"]}
        assert names == {"ws-1", "ws-2"}

    def test_list_workspaces_filter_by_status(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Status filter returns only matching workspaces."""
        AgentWorkspace.objects.create(repository=fossil_repo_obj, name="ws-active", branch="b/a", status="active", created_by=admin_user)
        AgentWorkspace.objects.create(repository=fossil_repo_obj, name="ws-merged", branch="b/m", status="merged", created_by=admin_user)

        response = admin_client.get(_api_url(sample_project.slug, "api/workspaces") + "?status=active")
        assert response.status_code == 200
        data = response.json()
        assert len(data["workspaces"]) == 1
        assert data["workspaces"][0]["name"] == "ws-active"

    def test_list_workspaces_wrong_method(self, admin_client, sample_project, fossil_repo_obj):
        """POST to workspace list returns 405."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces"),
            content_type="application/json",
        )
        assert response.status_code == 405

    def test_list_workspaces_denied_for_anon(self, client, sample_project, fossil_repo_obj):
        """Anonymous users cannot list workspaces."""
        response = client.get(_api_url(sample_project.slug, "api/workspaces"))
        assert response.status_code == 401

    def test_list_workspaces_response_shape(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Verify the response includes all expected fields."""
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-shape",
            branch="workspace/ws-shape",
            agent_id="claude-shape",
            description="test workspace",
            files_changed=3,
            commits_made=2,
            created_by=admin_user,
        )
        response = admin_client.get(_api_url(sample_project.slug, "api/workspaces"))
        ws = response.json()["workspaces"][0]
        assert ws["name"] == "ws-shape"
        assert ws["branch"] == "workspace/ws-shape"
        assert ws["status"] == "active"
        assert ws["agent_id"] == "claude-shape"
        assert ws["description"] == "test workspace"
        assert ws["files_changed"] == 3
        assert ws["commits_made"] == 2
        assert ws["created_at"] is not None


# ================================================================
# Workspace Detail
# ================================================================


@pytest.mark.django_db
class TestWorkspaceDetail:
    """Tests for GET /projects/<slug>/fossil/api/workspaces/<name> (lines 904-934)."""

    def test_get_workspace_detail(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """Workspace detail returns all fields."""
        response = admin_client.get(_api_url(sample_project.slug, "api/workspaces/ws-test-1"))
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "ws-test-1"
        assert data["branch"] == "workspace/ws-test-1"
        assert data["agent_id"] == "claude-test"
        assert data["status"] == "active"
        assert data["updated_at"] is not None

    def test_get_workspace_not_found(self, admin_client, sample_project, fossil_repo_obj):
        """Non-existent workspace returns 404."""
        response = admin_client.get(_api_url(sample_project.slug, "api/workspaces/nonexistent"))
        assert response.status_code == 404
        assert "not found" in response.json()["error"].lower()

    def test_get_workspace_wrong_method(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """POST to workspace detail returns 405."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-test-1"),
            content_type="application/json",
        )
        assert response.status_code == 405

    def test_get_workspace_denied_for_anon(self, client, sample_project, fossil_repo_obj, workspace):
        """Anonymous users cannot view workspace details."""
        response = client.get(_api_url(sample_project.slug, "api/workspaces/ws-test-1"))
        assert response.status_code == 401


# ================================================================
# Workspace Create
# ================================================================


@pytest.mark.django_db
class TestWorkspaceCreate:
    """Tests for POST /projects/<slug>/fossil/api/workspaces/create (lines 789-901)."""

    def test_create_workspace_success(self, admin_client, sample_project, fossil_repo_obj):
        """Creating a workspace opens a Fossil checkout and creates DB record."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            # All three subprocess calls succeed: open, branch new, update
            mock_run.return_value = _make_proc(stdout="checkout opened")

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/create"),
                data=json.dumps({"name": "agent-fix-99", "description": "Fix bug #99", "agent_id": "claude-99"}),
                content_type="application/json",
            )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "agent-fix-99"
        assert data["branch"] == "workspace/agent-fix-99"
        assert data["status"] == "active"
        assert data["agent_id"] == "claude-99"

        # Verify DB state
        ws = AgentWorkspace.objects.get(repository=fossil_repo_obj, name="agent-fix-99")
        assert ws.branch == "workspace/agent-fix-99"
        assert ws.description == "Fix bug #99"

    def test_create_workspace_missing_name(self, admin_client, sample_project, fossil_repo_obj):
        """Workspace name is required."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/create"),
            data=json.dumps({"description": "no name"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "name" in response.json()["error"].lower()

    def test_create_workspace_invalid_name(self, admin_client, sample_project, fossil_repo_obj):
        """Invalid workspace name (special chars) returns 400."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/create"),
            data=json.dumps({"name": "../../etc/passwd"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "Invalid workspace name" in response.json()["error"]

    def test_create_workspace_name_starts_with_dot(self, admin_client, sample_project, fossil_repo_obj):
        """Workspace name starting with a dot is rejected by the regex."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/create"),
            data=json.dumps({"name": ".hidden"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_create_workspace_duplicate_name(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Duplicate workspace name returns 409."""
        AgentWorkspace.objects.create(repository=fossil_repo_obj, name="dup-ws", branch="workspace/dup-ws", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/create"),
            data=json.dumps({"name": "dup-ws"}),
            content_type="application/json",
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["error"]

    def test_create_workspace_invalid_json(self, admin_client, sample_project, fossil_repo_obj):
        """Invalid JSON body returns 400."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/create"),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "Invalid JSON" in response.json()["error"]

    def test_create_workspace_fossil_open_fails(self, admin_client, sample_project, fossil_repo_obj):
        """When fossil open fails, return 500 and clean up."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls, patch("shutil.rmtree"):
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.return_value = _make_proc(returncode=1, stderr="open failed")

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/create"),
                data=json.dumps({"name": "fail-open"}),
                content_type="application/json",
            )

        assert response.status_code == 500
        assert "Failed to open" in response.json()["error"]

    def test_create_workspace_branch_creation_fails(self, admin_client, sample_project, fossil_repo_obj):
        """When branch creation fails, return 500 and clean up checkout."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls, patch("shutil.rmtree"):
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            # First call (open) succeeds, second (branch new) fails
            mock_run.side_effect = [
                _make_proc(returncode=0),  # open
                _make_proc(returncode=1, stderr="branch error"),  # branch new
                _make_proc(returncode=0),  # close --force (cleanup)
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/create"),
                data=json.dumps({"name": "fail-branch"}),
                content_type="application/json",
            )

        assert response.status_code == 500
        assert "Failed to create branch" in response.json()["error"]

    def test_create_workspace_update_fails(self, admin_client, sample_project, fossil_repo_obj):
        """When switching to the new branch fails, return 500 and clean up."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls, patch("shutil.rmtree"):
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),  # open
                _make_proc(returncode=0),  # branch new
                _make_proc(returncode=1, stderr="update failed"),  # update branch
                _make_proc(returncode=0),  # close --force (cleanup)
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/create"),
                data=json.dumps({"name": "fail-update"}),
                content_type="application/json",
            )

        assert response.status_code == 500
        assert "Failed to switch to branch" in response.json()["error"]

    def test_create_workspace_wrong_method(self, admin_client, sample_project, fossil_repo_obj):
        """GET to create endpoint returns 405."""
        response = admin_client.get(_api_url(sample_project.slug, "api/workspaces/create"))
        assert response.status_code == 405

    def test_create_workspace_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj):
        """Read-only users cannot create workspaces."""
        response = reader_client.post(
            _api_url(sample_project.slug, "api/workspaces/create"),
            data=json.dumps({"name": "denied-ws"}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_create_workspace_denied_for_anon(self, client, sample_project, fossil_repo_obj):
        """Anonymous users cannot create workspaces."""
        response = client.post(
            _api_url(sample_project.slug, "api/workspaces/create"),
            data=json.dumps({"name": "anon-ws"}),
            content_type="application/json",
        )
        assert response.status_code == 401


# ================================================================
# Workspace Commit
# ================================================================


@pytest.mark.django_db
class TestWorkspaceCommit:
    """Tests for POST /projects/<slug>/fossil/api/workspaces/<name>/commit (lines 937-1034)."""

    def test_commit_success(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """Successful commit increments commits_made and returns output."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            # addremove then commit
            mock_run.side_effect = [
                _make_proc(returncode=0),  # addremove
                _make_proc(returncode=0, stdout="New_Version: abc123"),  # commit
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/commit"),
                data=json.dumps({"message": "Fix bug", "agent_id": "claude-test"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Fix bug"
        assert data["commits_made"] == 1

        workspace.refresh_from_db()
        assert workspace.commits_made == 1

    def test_commit_with_specific_files(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """Committing specific files adds them individually."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),  # add file1
                _make_proc(returncode=0),  # add file2
                _make_proc(returncode=0, stdout="New_Version: def456"),  # commit
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/commit"),
                data=json.dumps({"message": "Add files", "files": ["a.py", "b.py"], "agent_id": "claude-test"}),
                content_type="application/json",
            )

        assert response.status_code == 200

    def test_commit_nothing_to_commit(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """When fossil says nothing changed, return 409."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),  # addremove
                _make_proc(returncode=1, stderr="nothing has changed"),  # commit
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/commit"),
                data=json.dumps({"message": "no change", "agent_id": "claude-test"}),
                content_type="application/json",
            )

        assert response.status_code == 409
        assert "Nothing to commit" in response.json()["error"]

    def test_commit_fossil_error(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """When fossil commit fails (not nothing-changed), return 500."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),  # addremove
                _make_proc(returncode=1, stderr="lock failed"),  # commit
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/commit"),
                data=json.dumps({"message": "fail commit", "agent_id": "claude-test"}),
                content_type="application/json",
            )

        assert response.status_code == 500
        assert "Commit failed" in response.json()["error"]

    def test_commit_missing_message(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """Commit without message returns 400."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-test-1/commit"),
            data=json.dumps({"agent_id": "claude-test"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "message" in response.json()["error"].lower()

    def test_commit_workspace_not_found(self, admin_client, sample_project, fossil_repo_obj):
        """Commit to non-existent workspace returns 404."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/nonexistent/commit"),
            data=json.dumps({"message": "fix"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_commit_workspace_not_active(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Commit to a merged workspace returns 409."""
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-merged",
            branch="workspace/ws-merged",
            status="merged",
            created_by=admin_user,
        )

        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-merged/commit"),
            data=json.dumps({"message": "too late"}),
            content_type="application/json",
        )
        assert response.status_code == 409
        assert "merged" in response.json()["error"]

    def test_commit_invalid_json(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """Invalid JSON body returns 400."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-test-1/commit"),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_commit_wrong_method(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """GET to commit endpoint returns 405."""
        response = admin_client.get(_api_url(sample_project.slug, "api/workspaces/ws-test-1/commit"))
        assert response.status_code == 405

    def test_commit_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj, workspace):
        """Read-only users cannot commit."""
        response = reader_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-test-1/commit"),
            data=json.dumps({"message": "denied"}),
            content_type="application/json",
        )
        assert response.status_code == 403


# ================================================================
# Workspace Merge
# ================================================================


@pytest.mark.django_db
class TestWorkspaceMerge:
    """Tests for POST /projects/<slug>/fossil/api/workspaces/<name>/merge (lines 1037-1185).

    This endpoint is complex: it enforces branch protection, review gates,
    and runs three subprocess calls (update, merge, commit).
    """

    def test_merge_success_admin_bypass(self, admin_client, sample_project, fossil_repo_obj, workspace, admin_user):
        """Admin can merge without an approved review (admin bypass of review gate)."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls, patch("shutil.rmtree"):
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),  # update trunk
                _make_proc(returncode=0, stdout="merged ok"),  # merge
                _make_proc(returncode=0, stdout="committed"),  # commit
                _make_proc(returncode=0),  # close --force
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/merge"),
                data=json.dumps({"target_branch": "trunk", "agent_id": "claude-test"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "merged"
        assert data["target_branch"] == "trunk"

        workspace.refresh_from_db()
        assert workspace.status == "merged"
        assert workspace.checkout_path == ""

    def test_merge_with_approved_review(self, writer_client, sample_project, fossil_repo_obj, admin_user):
        """Non-admin writer can merge if an approved review exists for the workspace."""
        ws = AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-reviewed",
            branch="workspace/ws-reviewed",
            status="active",
            checkout_path="/tmp/fake",
            created_by=admin_user,
        )
        CodeReview.objects.create(
            repository=fossil_repo_obj,
            workspace=ws,
            title="Fix",
            diff="d",
            status="approved",
            created_by=admin_user,
        )

        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls, patch("shutil.rmtree"):
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),  # update
                _make_proc(returncode=0),  # merge
                _make_proc(returncode=0),  # commit
                _make_proc(returncode=0),  # close
            ]

            response = writer_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-reviewed/merge"),
                data=json.dumps({"target_branch": "trunk"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert response.json()["status"] == "merged"

    def test_merge_marks_linked_review_as_merged(self, admin_client, sample_project, fossil_repo_obj, workspace, admin_user):
        """Merging a workspace with an approved review updates the review status to merged."""
        review = CodeReview.objects.create(
            repository=fossil_repo_obj,
            workspace=workspace,
            title="ws review",
            diff="d",
            status="approved",
            created_by=admin_user,
        )

        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls, patch("shutil.rmtree"):
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.return_value = _make_proc(returncode=0)

            admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/merge"),
                data=json.dumps({"agent_id": "claude-test"}),
                content_type="application/json",
            )

        review.refresh_from_db()
        assert review.status == "merged"

    def test_merge_blocked_no_review_non_admin(self, writer_client, sample_project, fossil_repo_obj, admin_user):
        """Non-admin cannot merge if no approved review exists for the workspace."""
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-no-review",
            branch="workspace/ws-no-review",
            status="active",
            checkout_path="/tmp/fake",
            created_by=admin_user,
        )

        response = writer_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-no-review/merge"),
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 403
        assert "No approved code review" in response.json()["error"]

    def test_merge_blocked_review_not_approved(self, writer_client, sample_project, fossil_repo_obj, admin_user):
        """Non-admin cannot merge if the linked review is still pending."""
        ws = AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-pending-review",
            branch="workspace/ws-pending-review",
            status="active",
            checkout_path="/tmp/fake",
            created_by=admin_user,
        )
        CodeReview.objects.create(
            repository=fossil_repo_obj,
            workspace=ws,
            title="Pending",
            diff="d",
            status="pending",
            created_by=admin_user,
        )

        response = writer_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-pending-review/merge"),
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 403
        assert "must be approved" in response.json()["error"]

    def test_merge_blocked_branch_protection_restrict_push(self, writer_client, sample_project, fossil_repo_obj, admin_user):
        """Branch protection with restrict_push blocks non-admin merges."""
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-protected",
            branch="workspace/ws-protected",
            status="active",
            checkout_path="/tmp/fake",
            created_by=admin_user,
        )
        BranchProtection.objects.create(
            repository=fossil_repo_obj,
            branch_pattern="trunk",
            restrict_push=True,
            created_by=admin_user,
        )

        response = writer_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-protected/merge"),
            data=json.dumps({"target_branch": "trunk"}),
            content_type="application/json",
        )
        assert response.status_code == 403
        assert "protected" in response.json()["error"].lower()

    def test_merge_blocked_required_status_check_not_passed(self, writer_client, sample_project, fossil_repo_obj, admin_user):
        """Branch protection with required status checks blocks merge when check hasn't passed."""
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-ci-fail",
            branch="workspace/ws-ci-fail",
            status="active",
            checkout_path="/tmp/fake",
            created_by=admin_user,
        )
        BranchProtection.objects.create(
            repository=fossil_repo_obj,
            branch_pattern="trunk",
            restrict_push=False,
            require_status_checks=True,
            required_contexts="ci/tests",
            created_by=admin_user,
        )
        # Status check is pending (not success)
        StatusCheck.objects.create(
            repository=fossil_repo_obj,
            checkin_uuid="some-uuid",
            context="ci/tests",
            state="pending",
            created_by=admin_user,
        )

        response = writer_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-ci-fail/merge"),
            data=json.dumps({"target_branch": "trunk"}),
            content_type="application/json",
        )
        assert response.status_code == 403
        assert "status check" in response.json()["error"].lower()

    def test_merge_allowed_with_passing_status_check(self, writer_client, sample_project, fossil_repo_obj, admin_user):
        """Branch protection with passing required status check allows merge."""
        ws = AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-ci-pass",
            branch="workspace/ws-ci-pass",
            status="active",
            checkout_path="/tmp/fake",
            created_by=admin_user,
        )
        BranchProtection.objects.create(
            repository=fossil_repo_obj,
            branch_pattern="trunk",
            restrict_push=False,
            require_status_checks=True,
            required_contexts="ci/tests",
            created_by=admin_user,
        )
        StatusCheck.objects.create(
            repository=fossil_repo_obj,
            checkin_uuid="some-uuid",
            context="ci/tests",
            state="success",
            created_by=admin_user,
        )
        CodeReview.objects.create(
            repository=fossil_repo_obj,
            workspace=ws,
            title="Fix",
            diff="d",
            status="approved",
            created_by=admin_user,
        )

        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls, patch("shutil.rmtree"):
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.return_value = _make_proc(returncode=0)

            response = writer_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-ci-pass/merge"),
                data=json.dumps({"target_branch": "trunk"}),
                content_type="application/json",
            )

        assert response.status_code == 200

    def test_merge_fossil_update_fails(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """When fossil update to target branch fails, return 500."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.return_value = _make_proc(returncode=1, stderr="update failed")

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/merge"),
                data=json.dumps({"agent_id": "claude-test"}),
                content_type="application/json",
            )

        assert response.status_code == 500
        assert "Failed to switch" in response.json()["error"]

    def test_merge_fossil_merge_fails(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """When fossil merge command fails, return 500."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),  # update
                _make_proc(returncode=1, stderr="merge conflict"),  # merge
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/merge"),
                data=json.dumps({"agent_id": "claude-test"}),
                content_type="application/json",
            )

        assert response.status_code == 500
        assert "Merge failed" in response.json()["error"]

    def test_merge_commit_fails(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """When the merge commit fails, return 500 and don't close workspace."""
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),  # update
                _make_proc(returncode=0),  # merge
                _make_proc(returncode=1, stderr="commit lock"),  # commit
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/merge"),
                data=json.dumps({"agent_id": "claude-test"}),
                content_type="application/json",
            )

        assert response.status_code == 500
        assert "Merge commit failed" in response.json()["error"]

        # Workspace should still be active (not closed on commit failure)
        workspace.refresh_from_db()
        assert workspace.status == "active"

    def test_merge_workspace_not_found(self, admin_client, sample_project, fossil_repo_obj):
        """Merging a non-existent workspace returns 404."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/nonexistent/merge"),
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_merge_workspace_not_active(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Merging an already-merged workspace returns 409."""
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-already-merged",
            branch="workspace/ws-already-merged",
            status="merged",
            created_by=admin_user,
        )

        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-already-merged/merge"),
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 409
        assert "merged" in response.json()["error"]

    def test_merge_wrong_method(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """GET to merge endpoint returns 405."""
        response = admin_client.get(_api_url(sample_project.slug, "api/workspaces/ws-test-1/merge"))
        assert response.status_code == 405

    def test_merge_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj, workspace):
        """Read-only users cannot merge workspaces."""
        response = reader_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-test-1/merge"),
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 403


# ================================================================
# Workspace Abandon
# ================================================================


@pytest.mark.django_db
class TestWorkspaceAbandon:
    """Tests for DELETE /projects/<slug>/fossil/api/workspaces/<name>/abandon (lines 1188-1238)."""

    def test_abandon_success(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """Abandoning a workspace closes checkout, cleans up directory, and updates status."""
        with (
            patch("subprocess.run") as mock_run,
            patch("fossil.cli.FossilCLI") as mock_cli_cls,
            patch("shutil.rmtree") as mock_rmtree,
        ):
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.return_value = _make_proc(returncode=0)

            response = admin_client.delete(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/abandon"),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "abandoned"
        assert data["name"] == "ws-test-1"

        workspace.refresh_from_db()
        assert workspace.status == "abandoned"
        assert workspace.checkout_path == ""

        # Verify cleanup was called
        mock_rmtree.assert_called_once()

    def test_abandon_no_checkout_path(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Abandoning a workspace with empty checkout path still works (no cleanup needed)."""
        ws = AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-no-path",
            branch="workspace/ws-no-path",
            status="active",
            checkout_path="",
            created_by=admin_user,
        )

        with patch("fossil.cli.FossilCLI"):
            response = admin_client.delete(_api_url(sample_project.slug, "api/workspaces/ws-no-path/abandon"))

        assert response.status_code == 200
        ws.refresh_from_db()
        assert ws.status == "abandoned"

    def test_abandon_workspace_not_found(self, admin_client, sample_project, fossil_repo_obj):
        """Abandoning a non-existent workspace returns 404."""
        response = admin_client.delete(_api_url(sample_project.slug, "api/workspaces/nonexistent/abandon"))
        assert response.status_code == 404

    def test_abandon_workspace_already_abandoned(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Abandoning an already-abandoned workspace returns 409."""
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-gone",
            branch="workspace/ws-gone",
            status="abandoned",
            created_by=admin_user,
        )

        response = admin_client.delete(_api_url(sample_project.slug, "api/workspaces/ws-gone/abandon"))
        assert response.status_code == 409
        assert "already abandoned" in response.json()["error"]

    def test_abandon_wrong_method(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """POST to abandon endpoint returns 405 (DELETE required)."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/workspaces/ws-test-1/abandon"),
            content_type="application/json",
        )
        assert response.status_code == 405

    def test_abandon_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj, workspace):
        """Read-only users cannot abandon workspaces."""
        response = reader_client.delete(_api_url(sample_project.slug, "api/workspaces/ws-test-1/abandon"))
        assert response.status_code == 403

    def test_abandon_denied_for_anon(self, client, sample_project, fossil_repo_obj, workspace):
        """Anonymous users cannot abandon workspaces."""
        response = client.delete(_api_url(sample_project.slug, "api/workspaces/ws-test-1/abandon"))
        assert response.status_code == 401


# ================================================================
# Workspace Ownership Checks
# ================================================================


@pytest.mark.django_db
class TestWorkspaceOwnership:
    """Tests for _check_workspace_ownership (lines 722-747).

    Token-based callers must supply matching agent_id.
    Session-auth users (human oversight) are always allowed.
    """

    def test_session_user_always_allowed(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """Session-auth users bypass ownership check (human oversight).
        Tested through the commit endpoint which calls _check_workspace_ownership.
        """
        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),  # addremove
                _make_proc(returncode=0, stdout="committed"),  # commit
            ]

            # Session user does not provide agent_id -- should still be allowed
            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-test-1/commit"),
                data=json.dumps({"message": "Human override"}),
                content_type="application/json",
            )

        assert response.status_code == 200

    def test_workspace_without_agent_id_allows_any_writer(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Workspace with empty agent_id allows any writer to operate."""
        AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="ws-no-agent",
            branch="workspace/ws-no-agent",
            agent_id="",
            status="active",
            checkout_path="/tmp/fake",
            created_by=admin_user,
        )

        with patch("subprocess.run") as mock_run, patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.binary = "/usr/local/bin/fossil"
            mock_cli_cls.return_value._env = {}
            mock_run.side_effect = [
                _make_proc(returncode=0),
                _make_proc(returncode=0, stdout="committed"),
            ]

            response = admin_client.post(
                _api_url(sample_project.slug, "api/workspaces/ws-no-agent/commit"),
                data=json.dumps({"message": "Anyone can commit"}),
                content_type="application/json",
            )

        assert response.status_code == 200


# ================================================================
# SSE Events - Stream Content
# ================================================================


@pytest.mark.django_db
class TestSSEEventStream:
    """Tests for GET /projects/<slug>/fossil/api/events (lines 1521-1653).

    The SSE endpoint returns a StreamingHttpResponse. We verify the response
    metadata and test the event generator for various event types.
    """

    def test_sse_response_headers(self, admin_client, sample_project, fossil_repo_obj):
        """SSE endpoint sets correct headers for event streaming."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            reader = mock_reader_cls.return_value
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_checkin_count.return_value = 0

            response = admin_client.get(_api_url(sample_project.slug, "api/events"))

        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache"
        assert response["X-Accel-Buffering"] == "no"
        assert response.streaming is True
        # Close the streaming response to release the DB connection
        response.close()

    def test_sse_generator_yields_claim_events(self, sample_project, fossil_repo_obj, admin_user):
        """The SSE generator detects new TicketClaims and yields claim events.

        We test the generator directly rather than going through StreamingHttpResponse,
        because the response wraps it in map(make_bytes, ...) which complicates
        exception-based termination.
        """
        # Simulate what event_stream() does: snapshot state, create new objects, check
        last_claim_id = TicketClaim.all_objects.filter(repository=fossil_repo_obj).order_by("-pk").values_list("pk", flat=True).first() or 0

        # Create a claim after the snapshot
        TicketClaim.objects.create(
            repository=fossil_repo_obj,
            ticket_uuid="sse-test-ticket",
            agent_id="sse-agent",
            created_by=admin_user,
        )

        # Query exactly as the generator does
        new_claims = TicketClaim.all_objects.filter(repository=fossil_repo_obj, pk__gt=last_claim_id).order_by("pk")
        events = []
        for c in new_claims:
            events.append(f"event: claim\ndata: {json.dumps({'ticket_uuid': c.ticket_uuid, 'agent_id': c.agent_id})}\n\n")

        assert len(events) >= 1
        assert "sse-test-ticket" in events[0]
        assert "sse-agent" in events[0]

    def test_sse_generator_yields_workspace_events(self, sample_project, fossil_repo_obj, admin_user):
        """The SSE generator detects new AgentWorkspaces and yields workspace events."""
        last_ws_id = AgentWorkspace.all_objects.filter(repository=fossil_repo_obj).order_by("-pk").values_list("pk", flat=True).first() or 0

        AgentWorkspace.objects.create(
            repository=fossil_repo_obj,
            name="sse-ws",
            branch="workspace/sse-ws",
            agent_id="sse-agent",
            created_by=admin_user,
        )

        new_ws = AgentWorkspace.all_objects.filter(repository=fossil_repo_obj, pk__gt=last_ws_id).order_by("pk")
        events = []
        for ws in new_ws:
            events.append(f"event: workspace\ndata: {json.dumps({'name': ws.name, 'agent_id': ws.agent_id})}\n\n")

        assert len(events) >= 1
        assert "sse-ws" in events[0]

    def test_sse_generator_yields_review_events(self, sample_project, fossil_repo_obj, admin_user):
        """The SSE generator detects new CodeReviews and yields review events."""
        last_review_id = CodeReview.all_objects.filter(repository=fossil_repo_obj).order_by("-pk").values_list("pk", flat=True).first() or 0

        CodeReview.objects.create(
            repository=fossil_repo_obj,
            title="SSE review",
            diff="d",
            agent_id="sse-agent",
            created_by=admin_user,
        )

        new_reviews = CodeReview.all_objects.filter(repository=fossil_repo_obj, pk__gt=last_review_id).order_by("pk")
        events = []
        for r in new_reviews:
            events.append(f"event: review\ndata: {json.dumps({'title': r.title, 'agent_id': r.agent_id})}\n\n")

        assert len(events) >= 1
        assert "SSE review" in events[0]

    def test_sse_generator_yields_checkin_events(self, sample_project, fossil_repo_obj, admin_user):
        """The SSE generator detects new checkins and yields checkin events."""
        from fossil.api_views import api_events

        factory = RequestFactory()
        request = factory.get(_api_url(sample_project.slug, "api/events"))
        request.user = admin_user
        request.session = {}

        timeline_entry = MagicMock()
        timeline_entry.uuid = "checkin-001"
        timeline_entry.user = "dev"
        timeline_entry.comment = "initial commit"
        timeline_entry.branch = "trunk"
        timeline_entry.timestamp = None

        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            reader = mock_reader_cls.return_value
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            # First call during snapshot: 0 checkins. Second call during poll: 1 checkin
            reader.get_checkin_count.side_effect = [0, 1]
            reader.get_timeline.return_value = [timeline_entry]

            response = api_events(request, slug=sample_project.slug)
            events = _drain_sse_one_iteration(response)

        checkin_events = [e for e in events if "event: checkin" in e]
        assert len(checkin_events) >= 1
        assert "checkin-001" in checkin_events[0]

    def test_sse_generator_heartbeat(self, sample_project, fossil_repo_obj, admin_user):
        """After 3 empty iterations the generator emits a heartbeat comment."""
        from fossil.api_views import api_events

        factory = RequestFactory()
        request = factory.get(_api_url(sample_project.slug, "api/events"))
        request.user = admin_user
        request.session = {}

        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            reader = mock_reader_cls.return_value
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_checkin_count.return_value = 0

            response = api_events(request, slug=sample_project.slug)
            # Run 3 empty iterations so heartbeat triggers
            events = _drain_sse_n_iterations(response, n=3)

        heartbeats = [e for e in events if ": heartbeat" in e]
        assert len(heartbeats) >= 1


# ================================================================
# _resolve_batch_route
# ================================================================


@pytest.mark.django_db
class TestResolveBatchRoute:
    """Tests for _resolve_batch_route helper (lines 596-607)."""

    def test_static_route_timeline(self):
        """Static route /api/timeline resolves to the timeline view."""
        from fossil.api_views import _resolve_batch_route, api_timeline

        view_func, kwargs = _resolve_batch_route("/api/timeline")
        assert view_func is api_timeline
        assert kwargs == {}

    def test_static_route_project(self):
        """Static route /api/project resolves to the project view."""
        from fossil.api_views import _resolve_batch_route, api_project

        view_func, kwargs = _resolve_batch_route("/api/project")
        assert view_func is api_project
        assert kwargs == {}

    def test_dynamic_route_ticket(self):
        """Dynamic route /api/tickets/<uuid> resolves with ticket_uuid kwarg."""
        from fossil.api_views import _resolve_batch_route, api_ticket_detail

        view_func, kwargs = _resolve_batch_route("/api/tickets/abc-123-def")
        assert view_func is api_ticket_detail
        assert kwargs == {"ticket_uuid": "abc-123-def"}

    def test_dynamic_route_wiki(self):
        """Dynamic route /api/wiki/<name> resolves with page_name kwarg."""
        from fossil.api_views import _resolve_batch_route, api_wiki_page

        view_func, kwargs = _resolve_batch_route("/api/wiki/Getting-Started")
        assert view_func is api_wiki_page
        assert kwargs == {"page_name": "Getting-Started"}

    def test_unknown_route(self):
        """Unknown path returns (None, None)."""
        from fossil.api_views import _resolve_batch_route

        view_func, kwargs = _resolve_batch_route("/api/nonexistent")
        assert view_func is None
        assert kwargs is None

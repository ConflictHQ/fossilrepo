"""Tests for agent coordination features: ticket claiming, SSE, code reviews.

Tests use session auth (admin_client) since the API endpoints accept session
cookies as well as Bearer tokens. We create Django-side objects directly rather
than going through Fossil's SQLite for ticket verification in claiming tests.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.agent_claims import TicketClaim
from fossil.code_reviews import CodeReview, ReviewComment
from fossil.models import FossilRepository
from fossil.workspaces import AgentWorkspace
from organization.models import Team
from projects.models import ProjectTeam


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """User with write access but not admin."""
    writer = User.objects.create_user(username="writer_coord", password="testpass123")
    team = Team.objects.create(name="Coord Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="writer_coord", password="testpass123")
    return client


@pytest.fixture
def reader_user(db, admin_user, sample_project):
    """User with read-only access."""
    reader = User.objects.create_user(username="reader_coord", password="testpass123")
    team = Team.objects.create(name="Coord Readers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(reader)
    ProjectTeam.objects.create(project=sample_project, team=team, role="read", created_by=admin_user)
    return reader


@pytest.fixture
def reader_client(reader_user):
    client = Client()
    client.login(username="reader_coord", password="testpass123")
    return client


@pytest.fixture
def workspace(fossil_repo_obj, admin_user):
    """An active agent workspace."""
    return AgentWorkspace.objects.create(
        repository=fossil_repo_obj,
        name="agent-fix-42",
        branch="workspace/agent-fix-42",
        agent_id="claude-test",
        status="active",
        created_by=admin_user,
    )


def _api_url(slug, path):
    return f"/projects/{slug}/fossil/{path}"


# A mock FossilReader.get_ticket_detail that returns a fake ticket
def _mock_ticket_detail(uuid):
    if uuid == "abc123def456":
        ticket = MagicMock()
        ticket.uuid = "abc123def456"
        ticket.title = "Test Bug"
        ticket.status = "Open"
        ticket.type = "Bug"
        ticket.priority = ""
        ticket.severity = ""
        ticket.created = None
        return ticket
    return None


def _mock_get_tickets(status=None, limit=1000):
    """Return a list of fake tickets for unclaimed listing."""
    t1 = MagicMock()
    t1.uuid = "ticket-111"
    t1.title = "Bug One"
    t1.status = "Open"
    t1.type = "Bug"
    t1.priority = "High"
    t1.severity = ""
    t1.created = None

    t2 = MagicMock()
    t2.uuid = "ticket-222"
    t2.title = "Bug Two"
    t2.status = "Open"
    t2.type = "Bug"
    t2.priority = "Medium"
    t2.severity = ""
    t2.created = None

    return [t1, t2]


# ===== Ticket Claiming Tests =====


@pytest.mark.django_db
class TestTicketClaim:
    def test_claim_ticket_success(self, admin_client, sample_project, fossil_repo_obj):
        """Claiming an unclaimed ticket returns 201."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            instance = mock_reader_cls.return_value
            instance.get_ticket_detail.side_effect = _mock_ticket_detail

            response = admin_client.post(
                _api_url(sample_project.slug, "api/tickets/abc123def456/claim"),
                data=json.dumps({"agent_id": "claude-abc"}),
                content_type="application/json",
            )

        assert response.status_code == 201
        data = response.json()
        assert data["ticket_uuid"] == "abc123def456"
        assert data["agent_id"] == "claude-abc"
        assert data["status"] == "claimed"

        # Verify DB state
        claim = TicketClaim.objects.get(repository=fossil_repo_obj, ticket_uuid="abc123def456")
        assert claim.agent_id == "claude-abc"
        assert claim.status == "claimed"

    def test_claim_ticket_with_workspace(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """Claiming with a workspace links the claim to the workspace."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            instance = mock_reader_cls.return_value
            instance.get_ticket_detail.side_effect = _mock_ticket_detail

            response = admin_client.post(
                _api_url(sample_project.slug, "api/tickets/abc123def456/claim"),
                data=json.dumps({"agent_id": "claude-abc", "workspace": "agent-fix-42"}),
                content_type="application/json",
            )

        assert response.status_code == 201
        claim = TicketClaim.objects.get(repository=fossil_repo_obj, ticket_uuid="abc123def456")
        assert claim.workspace == workspace

    def test_claim_already_claimed_by_other(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Claiming a ticket already claimed by another agent returns 409."""
        TicketClaim.objects.create(
            repository=fossil_repo_obj,
            ticket_uuid="abc123def456",
            agent_id="other-agent",
            created_by=admin_user,
        )

        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            instance = mock_reader_cls.return_value
            instance.get_ticket_detail.side_effect = _mock_ticket_detail

            response = admin_client.post(
                _api_url(sample_project.slug, "api/tickets/abc123def456/claim"),
                data=json.dumps({"agent_id": "claude-abc"}),
                content_type="application/json",
            )

        assert response.status_code == 409
        data = response.json()
        assert data["error"] == "Ticket already claimed"
        assert data["claimed_by"] == "other-agent"

    def test_claim_idempotent_same_agent(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Re-claiming a ticket by the same agent is idempotent (200, not 409)."""
        TicketClaim.objects.create(
            repository=fossil_repo_obj,
            ticket_uuid="abc123def456",
            agent_id="claude-abc",
            created_by=admin_user,
        )

        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            instance = mock_reader_cls.return_value
            instance.get_ticket_detail.side_effect = _mock_ticket_detail

            response = admin_client.post(
                _api_url(sample_project.slug, "api/tickets/abc123def456/claim"),
                data=json.dumps({"agent_id": "claude-abc"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert response.json()["message"] == "Already claimed by you"

    def test_claim_nonexistent_ticket(self, admin_client, sample_project, fossil_repo_obj):
        """Claiming a ticket that doesn't exist in Fossil returns 404."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            instance = mock_reader_cls.return_value
            instance.get_ticket_detail.return_value = None

            response = admin_client.post(
                _api_url(sample_project.slug, "api/tickets/nonexistent/claim"),
                data=json.dumps({"agent_id": "claude-abc"}),
                content_type="application/json",
            )

        assert response.status_code == 404
        assert "not found" in response.json()["error"].lower()

    def test_claim_missing_agent_id(self, admin_client, sample_project, fossil_repo_obj):
        """Claiming without agent_id returns 400."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/tickets/abc123def456/claim"),
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "agent_id" in response.json()["error"]

    def test_claim_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj):
        """Read-only users cannot claim tickets."""
        response = reader_client.post(
            _api_url(sample_project.slug, "api/tickets/abc123def456/claim"),
            data=json.dumps({"agent_id": "claude-abc"}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_claim_denied_for_anon(self, client, sample_project, fossil_repo_obj):
        """Anonymous users cannot claim tickets."""
        response = client.post(
            _api_url(sample_project.slug, "api/tickets/abc123def456/claim"),
            data=json.dumps({"agent_id": "claude-abc"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_claim_wrong_method(self, admin_client, sample_project, fossil_repo_obj):
        """GET to claim endpoint returns 405."""
        response = admin_client.get(_api_url(sample_project.slug, "api/tickets/abc123def456/claim"))
        assert response.status_code == 405


@pytest.mark.django_db
class TestTicketRelease:
    def test_release_claim(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Releasing a claimed ticket soft-deletes the claim."""
        TicketClaim.objects.create(
            repository=fossil_repo_obj,
            ticket_uuid="abc123def456",
            agent_id="claude-abc",
            created_by=admin_user,
        )

        response = admin_client.post(
            _api_url(sample_project.slug, "api/tickets/abc123def456/release"),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "released"
        assert data["released_at"] is not None

        # Claim should be soft-deleted (not visible via default manager)
        assert TicketClaim.objects.filter(repository=fossil_repo_obj, ticket_uuid="abc123def456").count() == 0
        # But still in all_objects
        assert TicketClaim.all_objects.filter(repository=fossil_repo_obj, ticket_uuid="abc123def456").count() == 1

    def test_release_allows_reclaim(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """After releasing, another agent can claim the ticket."""
        TicketClaim.objects.create(
            repository=fossil_repo_obj,
            ticket_uuid="abc123def456",
            agent_id="claude-abc",
            created_by=admin_user,
        )

        # Release the claim
        admin_client.post(
            _api_url(sample_project.slug, "api/tickets/abc123def456/release"),
            content_type="application/json",
        )

        # Now a new claim should succeed
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            instance = mock_reader_cls.return_value
            instance.get_ticket_detail.side_effect = _mock_ticket_detail

            response = admin_client.post(
                _api_url(sample_project.slug, "api/tickets/abc123def456/claim"),
                data=json.dumps({"agent_id": "other-agent"}),
                content_type="application/json",
            )

        assert response.status_code == 201
        assert response.json()["agent_id"] == "other-agent"

    def test_release_nonexistent_claim(self, admin_client, sample_project, fossil_repo_obj):
        """Releasing when no claim exists returns 404."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/tickets/nonexistent/release"),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_release_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj):
        """Read-only users cannot release claims."""
        response = reader_client.post(
            _api_url(sample_project.slug, "api/tickets/abc123def456/release"),
            content_type="application/json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestTicketSubmit:
    def test_submit_work(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Submitting work updates claim status and records summary."""
        TicketClaim.objects.create(
            repository=fossil_repo_obj,
            ticket_uuid="abc123def456",
            agent_id="claude-abc",
            created_by=admin_user,
        )

        with patch("fossil.cli.FossilCLI") as mock_cli_cls:
            mock_cli_cls.return_value.ticket_change.return_value = True

            response = admin_client.post(
                _api_url(sample_project.slug, "api/tickets/abc123def456/submit"),
                data=json.dumps(
                    {
                        "summary": "Fixed the null pointer bug",
                        "files_changed": ["src/auth.py", "tests/test_auth.py"],
                    }
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "submitted"
        assert data["summary"] == "Fixed the null pointer bug"
        assert data["files_changed"] == ["src/auth.py", "tests/test_auth.py"]

        # Verify DB state
        claim = TicketClaim.objects.get(repository=fossil_repo_obj, ticket_uuid="abc123def456")
        assert claim.status == "submitted"

    def test_submit_already_submitted(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Submitting again on an already-submitted claim returns 409."""
        TicketClaim.objects.create(
            repository=fossil_repo_obj,
            ticket_uuid="abc123def456",
            agent_id="claude-abc",
            status="submitted",
            created_by=admin_user,
        )

        response = admin_client.post(
            _api_url(sample_project.slug, "api/tickets/abc123def456/submit"),
            data=json.dumps({"summary": "more work"}),
            content_type="application/json",
        )
        assert response.status_code == 409

    def test_submit_no_claim(self, admin_client, sample_project, fossil_repo_obj):
        """Submitting without an active claim returns 404."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/tickets/nonexistent/submit"),
            data=json.dumps({"summary": "some work"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_submit_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj):
        """Read-only users cannot submit work."""
        response = reader_client.post(
            _api_url(sample_project.slug, "api/tickets/abc123def456/submit"),
            data=json.dumps({"summary": "some work"}),
            content_type="application/json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestTicketsUnclaimed:
    def test_list_unclaimed(self, admin_client, sample_project, fossil_repo_obj):
        """Returns tickets that have no active claims."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            instance = mock_reader_cls.return_value
            instance.get_tickets.side_effect = _mock_get_tickets

            response = admin_client.get(
                _api_url(sample_project.slug, "api/tickets/unclaimed"),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        uuids = [t["uuid"] for t in data["tickets"]]
        assert "ticket-111" in uuids
        assert "ticket-222" in uuids

    def test_unclaimed_excludes_claimed(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Claimed tickets are excluded from unclaimed listing."""
        TicketClaim.objects.create(
            repository=fossil_repo_obj,
            ticket_uuid="ticket-111",
            agent_id="claude-abc",
            created_by=admin_user,
        )

        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            instance = mock_reader_cls.return_value
            instance.get_tickets.side_effect = _mock_get_tickets

            response = admin_client.get(
                _api_url(sample_project.slug, "api/tickets/unclaimed"),
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["tickets"][0]["uuid"] == "ticket-222"

    def test_unclaimed_denied_for_anon(self, client, sample_project, fossil_repo_obj):
        """Anonymous users cannot list unclaimed tickets."""
        response = client.get(_api_url(sample_project.slug, "api/tickets/unclaimed"))
        assert response.status_code == 401


# ===== SSE Tests =====


@pytest.mark.django_db
class TestSSEEvents:
    def test_events_endpoint_returns_sse(self, admin_client, sample_project, fossil_repo_obj):
        """SSE endpoint returns text/event-stream content type."""
        with patch("fossil.api_views.FossilReader") as mock_reader_cls:
            instance = mock_reader_cls.return_value
            instance.get_checkin_count.return_value = 0

            response = admin_client.get(
                _api_url(sample_project.slug, "api/events"),
            )

        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache"
        assert response["X-Accel-Buffering"] == "no"
        # It's a StreamingHttpResponse
        assert response.streaming is True

    def test_events_wrong_method(self, admin_client, sample_project, fossil_repo_obj):
        """POST to events endpoint returns 405."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/events"),
            content_type="application/json",
        )
        assert response.status_code == 405

    def test_events_denied_for_anon(self, client, sample_project, fossil_repo_obj):
        """Anonymous users cannot subscribe to events."""
        response = client.get(_api_url(sample_project.slug, "api/events"))
        assert response.status_code == 401


# ===== Code Review Tests =====


@pytest.mark.django_db
class TestCodeReviewCreate:
    def test_create_review(self, admin_client, sample_project, fossil_repo_obj):
        """Creating a review returns 201 with review data."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/reviews/create"),
            data=json.dumps(
                {
                    "title": "Fix null pointer in auth",
                    "description": "The auth check was failing when user is None",
                    "diff": "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1,3 +1,4 @@\n+# fix",
                    "files_changed": ["src/auth.py"],
                    "agent_id": "claude-abc",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Fix null pointer in auth"
        assert data["status"] == "pending"
        assert data["agent_id"] == "claude-abc"

        # Verify DB
        review = CodeReview.objects.get(pk=data["id"])
        assert review.title == "Fix null pointer in auth"
        assert review.diff.startswith("--- a/src/auth.py")

    def test_create_review_with_workspace(self, admin_client, sample_project, fossil_repo_obj, workspace):
        """Creating a review linked to a workspace."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/reviews/create"),
            data=json.dumps(
                {
                    "title": "Fix from workspace",
                    "diff": "--- a/foo.py\n+++ b/foo.py\n",
                    "workspace": "agent-fix-42",
                    "agent_id": "claude-test",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 201
        review = CodeReview.objects.get(pk=response.json()["id"])
        assert review.workspace == workspace

    def test_create_review_missing_title(self, admin_client, sample_project, fossil_repo_obj):
        """Creating a review without title returns 400."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/reviews/create"),
            data=json.dumps({"diff": "some diff"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "title" in response.json()["error"].lower()

    def test_create_review_missing_diff(self, admin_client, sample_project, fossil_repo_obj):
        """Creating a review without diff returns 400."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/reviews/create"),
            data=json.dumps({"title": "Some review"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "diff" in response.json()["error"].lower()

    def test_create_review_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj):
        """Read-only users cannot create reviews."""
        response = reader_client.post(
            _api_url(sample_project.slug, "api/reviews/create"),
            data=json.dumps({"title": "Fix", "diff": "---"}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_create_review_denied_for_anon(self, client, sample_project, fossil_repo_obj):
        """Anonymous users cannot create reviews."""
        response = client.post(
            _api_url(sample_project.slug, "api/reviews/create"),
            data=json.dumps({"title": "Fix", "diff": "---"}),
            content_type="application/json",
        )
        assert response.status_code == 401


@pytest.mark.django_db
class TestCodeReviewList:
    def test_list_reviews(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """List reviews returns all reviews for the repo."""
        CodeReview.objects.create(repository=fossil_repo_obj, title="Review A", diff="diff A", agent_id="a1", created_by=admin_user)
        CodeReview.objects.create(repository=fossil_repo_obj, title="Review B", diff="diff B", agent_id="a2", created_by=admin_user)

        response = admin_client.get(_api_url(sample_project.slug, "api/reviews"))
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    def test_list_reviews_filter_status(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Filtering by status returns only matching reviews."""
        CodeReview.objects.create(repository=fossil_repo_obj, title="Pending", diff="d", status="pending", created_by=admin_user)
        CodeReview.objects.create(repository=fossil_repo_obj, title="Approved", diff="d", status="approved", created_by=admin_user)

        response = admin_client.get(_api_url(sample_project.slug, "api/reviews") + "?status=approved")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["reviews"][0]["status"] == "approved"

    def test_list_reviews_denied_for_anon(self, client, sample_project, fossil_repo_obj):
        """Anonymous users cannot list reviews."""
        response = client.get(_api_url(sample_project.slug, "api/reviews"))
        assert response.status_code == 401


@pytest.mark.django_db
class TestCodeReviewDetail:
    def test_get_review_detail(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Getting review detail returns the review with comments."""
        review = CodeReview.objects.create(
            repository=fossil_repo_obj, title="Fix Auth", diff="--- diff ---", agent_id="claude", created_by=admin_user
        )
        ReviewComment.objects.create(review=review, body="LGTM", author="reviewer", created_by=admin_user)

        response = admin_client.get(_api_url(sample_project.slug, f"api/reviews/{review.pk}"))
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Fix Auth"
        assert data["diff"] == "--- diff ---"
        assert len(data["comments"]) == 1
        assert data["comments"][0]["body"] == "LGTM"

    def test_get_review_not_found(self, admin_client, sample_project, fossil_repo_obj):
        """Getting a non-existent review returns 404."""
        response = admin_client.get(_api_url(sample_project.slug, "api/reviews/99999"))
        assert response.status_code == 404


@pytest.mark.django_db
class TestCodeReviewComment:
    def test_add_comment(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Adding a comment to a review returns 201."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/comment"),
            data=json.dumps(
                {
                    "body": "Consider using a guard clause here",
                    "file_path": "src/auth.py",
                    "line_number": 42,
                    "author": "human-reviewer",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["body"] == "Consider using a guard clause here"
        assert data["file_path"] == "src/auth.py"
        assert data["line_number"] == 42
        assert data["author"] == "human-reviewer"

        # Verify DB
        assert ReviewComment.objects.filter(review=review).count() == 1

    def test_add_comment_infers_author_from_user(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """When author is omitted, the logged-in username is used."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/comment"),
            data=json.dumps({"body": "Nice fix"}),
            content_type="application/json",
        )

        assert response.status_code == 201
        assert response.json()["author"] == "admin"

    def test_add_comment_missing_body(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Adding a comment without body returns 400."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/comment"),
            data=json.dumps({"author": "someone"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_add_comment_review_not_found(self, admin_client, sample_project, fossil_repo_obj):
        """Adding a comment to non-existent review returns 404."""
        response = admin_client.post(
            _api_url(sample_project.slug, "api/reviews/99999/comment"),
            data=json.dumps({"body": "comment", "author": "me"}),
            content_type="application/json",
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestCodeReviewApprove:
    def test_approve_review(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Approving a pending review changes status to approved."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", status="pending", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/approve"),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["status"] == "approved"
        review.refresh_from_db()
        assert review.status == "approved"

    def test_approve_merged_review_fails(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Cannot approve an already-merged review."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", status="merged", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/approve"),
            content_type="application/json",
        )
        assert response.status_code == 409

    def test_approve_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj, admin_user):
        """Read-only users cannot approve reviews."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", created_by=admin_user)

        response = reader_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/approve"),
            content_type="application/json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestCodeReviewRequestChanges:
    def test_request_changes(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Requesting changes updates status and optionally adds a comment."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", status="pending", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/request-changes"),
            data=json.dumps({"comment": "Please fix the error handling"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["status"] == "changes_requested"

        review.refresh_from_db()
        assert review.status == "changes_requested"
        # Comment should be added
        assert review.comments.count() == 1
        assert review.comments.first().body == "Please fix the error handling"

    def test_request_changes_without_comment(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Requesting changes without a comment still updates status."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", status="pending", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/request-changes"),
            content_type="application/json",
        )

        assert response.status_code == 200
        review.refresh_from_db()
        assert review.status == "changes_requested"
        assert review.comments.count() == 0

    def test_request_changes_on_merged(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Cannot request changes on a merged review."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", status="merged", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/request-changes"),
            content_type="application/json",
        )
        assert response.status_code == 409


@pytest.mark.django_db
class TestCodeReviewMerge:
    def test_merge_approved_review(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Merging an approved review changes status to merged."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", status="approved", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/merge"),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["status"] == "merged"
        review.refresh_from_db()
        assert review.status == "merged"

    def test_merge_unapproved_review_fails(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Cannot merge a review that isn't approved."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", status="pending", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/merge"),
            content_type="application/json",
        )
        assert response.status_code == 409
        assert "approved" in response.json()["error"].lower()

    def test_merge_already_merged_review_fails(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Cannot merge a review twice."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", status="merged", created_by=admin_user)

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/merge"),
            content_type="application/json",
        )
        assert response.status_code == 409

    def test_merge_updates_linked_ticket_claim(self, admin_client, sample_project, fossil_repo_obj, admin_user):
        """Merging a review linked to a ticket updates the claim status."""
        claim = TicketClaim.objects.create(
            repository=fossil_repo_obj,
            ticket_uuid="ticket-999",
            agent_id="claude-abc",
            status="submitted",
            created_by=admin_user,
        )
        review = CodeReview.objects.create(
            repository=fossil_repo_obj,
            title="Fix for ticket-999",
            diff="d",
            status="approved",
            ticket_uuid="ticket-999",
            created_by=admin_user,
        )

        response = admin_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/merge"),
            content_type="application/json",
        )

        assert response.status_code == 200
        claim.refresh_from_db()
        assert claim.status == "merged"

    def test_merge_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj, admin_user):
        """Read-only users cannot merge reviews."""
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", status="approved", created_by=admin_user)

        response = reader_client.post(
            _api_url(sample_project.slug, f"api/reviews/{review.pk}/merge"),
            content_type="application/json",
        )
        assert response.status_code == 403


# ===== Model Tests =====


@pytest.mark.django_db
class TestTicketClaimModel:
    def test_str(self, fossil_repo_obj, admin_user):
        claim = TicketClaim.objects.create(repository=fossil_repo_obj, ticket_uuid="abc123def456", agent_id="claude", created_by=admin_user)
        s = str(claim)
        assert "abc123def456" in s
        assert "claude" in s

    def test_soft_delete(self, fossil_repo_obj, admin_user):
        claim = TicketClaim.objects.create(repository=fossil_repo_obj, ticket_uuid="abc123def456", agent_id="claude", created_by=admin_user)
        claim.soft_delete(user=admin_user)
        assert TicketClaim.objects.filter(pk=claim.pk).count() == 0
        assert TicketClaim.all_objects.filter(pk=claim.pk).count() == 1

    def test_multiple_claims_allowed_after_soft_delete(self, fossil_repo_obj, admin_user):
        """After soft-deleting a claim, a new claim for the same ticket can be created."""
        claim1 = TicketClaim.objects.create(
            repository=fossil_repo_obj, ticket_uuid="abc123def456", agent_id="agent-1", created_by=admin_user
        )
        claim1.soft_delete(user=admin_user)

        # New claim should succeed since original is soft-deleted
        claim2 = TicketClaim.objects.create(
            repository=fossil_repo_obj, ticket_uuid="abc123def456", agent_id="agent-2", created_by=admin_user
        )
        assert claim2.agent_id == "agent-2"
        # Both exist in all_objects
        assert TicketClaim.all_objects.filter(repository=fossil_repo_obj, ticket_uuid="abc123def456").count() == 2
        # Only the active one in default manager
        assert TicketClaim.objects.filter(repository=fossil_repo_obj, ticket_uuid="abc123def456").count() == 1


@pytest.mark.django_db
class TestCodeReviewModel:
    def test_str(self, fossil_repo_obj, admin_user):
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix Auth", diff="d", created_by=admin_user)
        s = str(review)
        assert "Fix Auth" in s
        assert "pending" in s

    def test_soft_delete(self, fossil_repo_obj, admin_user):
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", created_by=admin_user)
        review.soft_delete(user=admin_user)
        assert CodeReview.objects.filter(pk=review.pk).count() == 0
        assert CodeReview.all_objects.filter(pk=review.pk).count() == 1


@pytest.mark.django_db
class TestReviewCommentModel:
    def test_str_with_file(self, fossil_repo_obj, admin_user):
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", created_by=admin_user)
        comment = ReviewComment.objects.create(
            review=review, body="fix this", author="reviewer", file_path="src/auth.py", line_number=42, created_by=admin_user
        )
        s = str(comment)
        assert "src/auth.py:42" in s

    def test_str_without_file(self, fossil_repo_obj, admin_user):
        review = CodeReview.objects.create(repository=fossil_repo_obj, title="Fix", diff="d", created_by=admin_user)
        comment = ReviewComment.objects.create(review=review, body="looks good", author="reviewer", created_by=admin_user)
        s = str(comment)
        assert "general" in s

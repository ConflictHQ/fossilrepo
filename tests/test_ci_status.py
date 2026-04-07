import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.api_tokens import APIToken
from fossil.ci import StatusCheck
from fossil.models import FossilRepository
from organization.models import Team
from projects.models import ProjectTeam


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def api_token(fossil_repo_obj, admin_user):
    """Create an API token and return (APIToken instance, raw_token)."""
    raw, token_hash, prefix = APIToken.generate()
    token = APIToken.objects.create(
        repository=fossil_repo_obj,
        name="CI Token",
        token_hash=token_hash,
        token_prefix=prefix,
        permissions="status:write",
        created_by=admin_user,
    )
    return token, raw


@pytest.fixture
def status_check(fossil_repo_obj):
    return StatusCheck.objects.create(
        repository=fossil_repo_obj,
        checkin_uuid="abc123def456",
        context="ci/tests",
        state="success",
        description="All 42 tests passed",
        target_url="https://ci.example.com/build/1",
    )


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """User with write access but not admin."""
    writer = User.objects.create_user(username="writer_ci", password="testpass123")
    team = Team.objects.create(name="CI Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="writer_ci", password="testpass123")
    return client


# --- StatusCheck Model Tests ---


@pytest.mark.django_db
class TestStatusCheckModel:
    def test_create_status_check(self, status_check):
        assert status_check.pk is not None
        assert str(status_check) == "ci/tests: success @ abc123def4"

    def test_soft_delete(self, status_check, admin_user):
        status_check.soft_delete(user=admin_user)
        assert status_check.is_deleted
        assert StatusCheck.objects.filter(pk=status_check.pk).count() == 0
        assert StatusCheck.all_objects.filter(pk=status_check.pk).count() == 1

    def test_unique_together(self, fossil_repo_obj):
        StatusCheck.objects.create(
            repository=fossil_repo_obj,
            checkin_uuid="unique123",
            context="ci/lint",
            state="pending",
        )
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            StatusCheck.objects.create(
                repository=fossil_repo_obj,
                checkin_uuid="unique123",
                context="ci/lint",
                state="success",
            )

    def test_ordering(self, fossil_repo_obj):
        c1 = StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="ord1", context="ci/first", state="pending")
        c2 = StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="ord2", context="ci/second", state="success")
        checks = list(StatusCheck.objects.filter(repository=fossil_repo_obj))
        assert checks[0] == c2  # newest first
        assert checks[1] == c1

    def test_state_choices(self):
        assert "pending" in StatusCheck.State.values
        assert "success" in StatusCheck.State.values
        assert "failure" in StatusCheck.State.values
        assert "error" in StatusCheck.State.values


# --- Status Check API POST Tests ---


@pytest.mark.django_db
class TestStatusCheckAPIPost:
    def test_post_creates_status_check(self, client, sample_project, fossil_repo_obj, api_token):
        token_obj, raw_token = api_token
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps(
                {
                    "checkin": "deadbeef123",
                    "context": "ci/tests",
                    "state": "success",
                    "description": "All tests passed",
                    "target_url": "https://ci.example.com/build/42",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["context"] == "ci/tests"
        assert data["state"] == "success"
        assert data["created"] is True

        check = StatusCheck.objects.get(repository=fossil_repo_obj, checkin_uuid="deadbeef123", context="ci/tests")
        assert check.state == "success"
        assert check.description == "All tests passed"

    def test_post_updates_existing_check(self, client, sample_project, fossil_repo_obj, api_token):
        token_obj, raw_token = api_token
        # Create initial check
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="update123", context="ci/tests", state="pending")
        # Update it
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps(
                {
                    "checkin": "update123",
                    "context": "ci/tests",
                    "state": "success",
                    "description": "Now passing",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is False
        assert data["state"] == "success"

    def test_post_without_token_returns_401(self, client, sample_project, fossil_repo_obj):
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps({"checkin": "abc", "context": "ci/tests", "state": "success"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_post_with_invalid_token_returns_401(self, client, sample_project, fossil_repo_obj):
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps({"checkin": "abc", "context": "ci/tests", "state": "success"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer invalid_token_xyz",
        )
        assert response.status_code == 401

    def test_post_with_wrong_repo_token_returns_401(self, client, sample_project, fossil_repo_obj, admin_user, org):
        """Token scoped to a different repo should fail."""
        from projects.models import Project

        other_project = Project.objects.create(name="Other Project", organization=org, visibility="private", created_by=admin_user)
        other_repo = FossilRepository.objects.get(project=other_project, deleted_at__isnull=True)
        raw, token_hash, prefix = APIToken.generate()
        APIToken.objects.create(
            repository=other_repo, name="Other Token", token_hash=token_hash, token_prefix=prefix, created_by=admin_user
        )

        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps({"checkin": "abc", "context": "ci/tests", "state": "success"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        assert response.status_code == 401

    def test_post_missing_checkin_returns_400(self, client, sample_project, fossil_repo_obj, api_token):
        _, raw_token = api_token
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps({"context": "ci/tests", "state": "success"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )
        assert response.status_code == 400
        assert "checkin" in response.json()["error"]

    def test_post_missing_context_returns_400(self, client, sample_project, fossil_repo_obj, api_token):
        _, raw_token = api_token
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps({"checkin": "abc123", "state": "success"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )
        assert response.status_code == 400
        assert "context" in response.json()["error"]

    def test_post_invalid_state_returns_400(self, client, sample_project, fossil_repo_obj, api_token):
        _, raw_token = api_token
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps({"checkin": "abc123", "context": "ci/tests", "state": "bogus"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )
        assert response.status_code == 400
        assert "state" in response.json()["error"]

    def test_post_invalid_json_returns_400(self, client, sample_project, fossil_repo_obj, api_token):
        _, raw_token = api_token
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data="not json",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )
        assert response.status_code == 400

    def test_post_expired_token_returns_401(self, client, sample_project, fossil_repo_obj, admin_user):
        from datetime import timedelta

        from django.utils import timezone

        raw, token_hash, prefix = APIToken.generate()
        APIToken.objects.create(
            repository=fossil_repo_obj,
            name="Expired Token",
            token_hash=token_hash,
            token_prefix=prefix,
            expires_at=timezone.now() - timedelta(days=1),
            created_by=admin_user,
        )
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps({"checkin": "abc", "context": "ci/tests", "state": "success"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        assert response.status_code == 401

    def test_post_token_without_status_write_returns_403(self, client, sample_project, fossil_repo_obj, admin_user):
        raw, token_hash, prefix = APIToken.generate()
        APIToken.objects.create(
            repository=fossil_repo_obj,
            name="Read Only Token",
            token_hash=token_hash,
            token_prefix=prefix,
            permissions="status:read",
            created_by=admin_user,
        )
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/api/status",
            data=json.dumps({"checkin": "abc", "context": "ci/tests", "state": "success"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        assert response.status_code == 403

    def test_method_not_allowed(self, client, sample_project, fossil_repo_obj):
        response = client.delete(f"/projects/{sample_project.slug}/fossil/api/status")
        assert response.status_code == 405


# --- Status Check API GET Tests ---


@pytest.mark.django_db
class TestStatusCheckAPIGet:
    def test_get_checks_for_checkin(self, admin_client, sample_project, fossil_repo_obj, status_check):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/api/status?checkin={status_check.checkin_uuid}")
        assert response.status_code == 200
        data = response.json()
        assert len(data["checks"]) == 1
        assert data["checks"][0]["context"] == "ci/tests"
        assert data["checks"][0]["state"] == "success"

    def test_get_without_checkin_param_returns_400(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/api/status")
        assert response.status_code == 400

    def test_get_empty_results(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/api/status?checkin=nonexistent")
        assert response.status_code == 200
        assert len(response.json()["checks"]) == 0


# --- Status Badge Tests ---


@pytest.mark.django_db
class TestStatusBadge:
    def test_badge_unknown_no_checks(self, client, sample_project, fossil_repo_obj):
        response = client.get(f"/projects/{sample_project.slug}/fossil/api/status/abc123/badge.svg")
        assert response.status_code == 200
        assert response["Content-Type"] == "image/svg+xml"
        assert "unknown" in response.content.decode()

    def test_badge_passing(self, client, sample_project, fossil_repo_obj):
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="pass123", context="ci/tests", state="success")
        response = client.get(f"/projects/{sample_project.slug}/fossil/api/status/pass123/badge.svg")
        assert response.status_code == 200
        assert "passing" in response.content.decode()

    def test_badge_failing(self, client, sample_project, fossil_repo_obj):
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="fail123", context="ci/tests", state="failure")
        response = client.get(f"/projects/{sample_project.slug}/fossil/api/status/fail123/badge.svg")
        assert response.status_code == 200
        assert "failing" in response.content.decode()

    def test_badge_pending(self, client, sample_project, fossil_repo_obj):
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="pend123", context="ci/tests", state="pending")
        response = client.get(f"/projects/{sample_project.slug}/fossil/api/status/pend123/badge.svg")
        assert response.status_code == 200
        assert "pending" in response.content.decode()

    def test_badge_mixed_failing_wins(self, client, sample_project, fossil_repo_obj):
        """If any check is failing, the aggregate badge should say 'failing'."""
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="mixed123", context="ci/tests", state="success")
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="mixed123", context="ci/lint", state="failure")
        response = client.get(f"/projects/{sample_project.slug}/fossil/api/status/mixed123/badge.svg")
        assert "failing" in response.content.decode()

    def test_badge_nonexistent_project_returns_404(self, client):
        response = client.get("/projects/nonexistent-project/fossil/api/status/abc/badge.svg")
        assert response.status_code == 404

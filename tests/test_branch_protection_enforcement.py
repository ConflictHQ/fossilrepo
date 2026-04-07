"""Tests for branch protection enforcement in the fossil_xfer proxy view.

Verifies that BranchProtection rules with restrict_push=True and/or
require_status_checks=True actually downgrade non-admin users from push
(--localauth) to read-only access.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.branch_protection import BranchProtection
from fossil.ci import StatusCheck
from fossil.models import FossilRepository
from organization.models import Team
from projects.models import ProjectTeam


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """User with write access but not admin."""
    writer = User.objects.create_user(username="writer_xfer", password="testpass123")
    team = Team.objects.create(name="Xfer Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="writer_xfer", password="testpass123")
    return client


@pytest.fixture
def admin_team_for_admin(db, admin_user, sample_project):
    """Ensure admin_user has an explicit admin team role on sample_project."""
    team = Team.objects.create(name="Admin Team", organization=sample_project.organization, created_by=admin_user)
    team.members.add(admin_user)
    ProjectTeam.objects.create(project=sample_project, team=team, role="admin", created_by=admin_user)
    return team


@pytest.fixture
def protection_rule(fossil_repo_obj, admin_user):
    return BranchProtection.objects.create(
        repository=fossil_repo_obj,
        branch_pattern="trunk",
        restrict_push=True,
        created_by=admin_user,
    )


@pytest.fixture
def protection_with_checks(fossil_repo_obj, admin_user):
    return BranchProtection.objects.create(
        repository=fossil_repo_obj,
        branch_pattern="trunk",
        restrict_push=False,
        require_status_checks=True,
        required_contexts="ci/tests\nci/lint",
        created_by=admin_user,
    )


def _get_localauth(mock_proxy):
    """Extract the localauth argument from a mock call to FossilCLI.http_proxy."""
    call_args = mock_proxy.call_args
    if "localauth" in call_args.kwargs:
        return call_args.kwargs["localauth"]
    return call_args.args[3]


# --- matches_branch helper ---


@pytest.mark.django_db
class TestMatchesBranch:
    def test_exact_match(self, protection_rule):
        assert protection_rule.matches_branch("trunk") is True

    def test_no_match(self, protection_rule):
        assert protection_rule.matches_branch("develop") is False

    def test_glob_pattern(self, fossil_repo_obj, admin_user):
        rule = BranchProtection.objects.create(
            repository=fossil_repo_obj,
            branch_pattern="release-*",
            restrict_push=True,
            created_by=admin_user,
        )
        assert rule.matches_branch("release-1.0") is True
        assert rule.matches_branch("release-") is True
        assert rule.matches_branch("develop") is False

    def test_wildcard_all(self, fossil_repo_obj, admin_user):
        rule = BranchProtection.objects.create(
            repository=fossil_repo_obj,
            branch_pattern="*",
            restrict_push=True,
            created_by=admin_user,
        )
        assert rule.matches_branch("trunk") is True
        assert rule.matches_branch("anything") is True


# --- fossil_xfer enforcement tests ---


MOCK_PROXY_RETURN = (b"response-body", "application/x-fossil")


def _exists_on_disk_true():
    """Property mock that always returns True for exists_on_disk."""
    return property(lambda self: True)


@pytest.mark.django_db
class TestXferBranchProtectionEnforcement:
    """Test that branch protection rules affect localauth in fossil_xfer."""

    def _post_xfer(self, client, slug):
        """POST to the fossil_xfer endpoint with dummy sync body."""
        return client.post(
            f"/projects/{slug}/fossil/xfer",
            data=b"xfer-body",
            content_type="application/x-fossil",
        )

    def test_no_protections_writer_gets_localauth(self, writer_client, sample_project, fossil_repo_obj):
        """Writer should get full push access when no protection rules exist."""
        with (
            patch("fossil.cli.FossilCLI.http_proxy", return_value=MOCK_PROXY_RETURN) as mock_proxy,
            patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=_exists_on_disk_true),
        ):
            response = self._post_xfer(writer_client, sample_project.slug)

        assert response.status_code == 200
        mock_proxy.assert_called_once()
        assert _get_localauth(mock_proxy) is True

    def test_restrict_push_writer_denied_localauth(self, writer_client, sample_project, fossil_repo_obj, protection_rule):
        """Writer should be downgraded to read-only when restrict_push is active."""
        with (
            patch("fossil.cli.FossilCLI.http_proxy", return_value=MOCK_PROXY_RETURN) as mock_proxy,
            patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=_exists_on_disk_true),
        ):
            response = self._post_xfer(writer_client, sample_project.slug)

        assert response.status_code == 200
        assert _get_localauth(mock_proxy) is False

    def test_restrict_push_admin_still_gets_localauth(
        self, admin_client, sample_project, fossil_repo_obj, protection_rule, admin_team_for_admin
    ):
        """Admins bypass branch protection and still get push access."""
        with (
            patch("fossil.cli.FossilCLI.http_proxy", return_value=MOCK_PROXY_RETURN) as mock_proxy,
            patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=_exists_on_disk_true),
        ):
            response = self._post_xfer(admin_client, sample_project.slug)

        assert response.status_code == 200
        assert _get_localauth(mock_proxy) is True

    def test_status_checks_passing_writer_gets_localauth(self, writer_client, sample_project, fossil_repo_obj, protection_with_checks):
        """Writer gets push access when all required status checks pass."""
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="latest1", context="ci/tests", state="success")
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="latest1", context="ci/lint", state="success")

        with (
            patch("fossil.cli.FossilCLI.http_proxy", return_value=MOCK_PROXY_RETURN) as mock_proxy,
            patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=_exists_on_disk_true),
        ):
            response = self._post_xfer(writer_client, sample_project.slug)

        assert response.status_code == 200
        assert _get_localauth(mock_proxy) is True

    def test_status_checks_failing_writer_denied_localauth(self, writer_client, sample_project, fossil_repo_obj, protection_with_checks):
        """Writer denied push when a required status check is failing."""
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="latest2", context="ci/tests", state="success")
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="latest2", context="ci/lint", state="failure")

        with (
            patch("fossil.cli.FossilCLI.http_proxy", return_value=MOCK_PROXY_RETURN) as mock_proxy,
            patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=_exists_on_disk_true),
        ):
            response = self._post_xfer(writer_client, sample_project.slug)

        assert response.status_code == 200
        assert _get_localauth(mock_proxy) is False

    def test_status_checks_missing_context_denies_localauth(self, writer_client, sample_project, fossil_repo_obj, protection_with_checks):
        """Writer denied push when a required context has no status check at all."""
        # Only create one of the two required checks
        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="latest3", context="ci/tests", state="success")

        with (
            patch("fossil.cli.FossilCLI.http_proxy", return_value=MOCK_PROXY_RETURN) as mock_proxy,
            patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=_exists_on_disk_true),
        ):
            response = self._post_xfer(writer_client, sample_project.slug)

        assert response.status_code == 200
        assert _get_localauth(mock_proxy) is False

    def test_soft_deleted_protection_not_enforced(self, writer_client, sample_project, fossil_repo_obj, protection_rule, admin_user):
        """Soft-deleted protection rules should not block push access."""
        protection_rule.soft_delete(user=admin_user)

        with (
            patch("fossil.cli.FossilCLI.http_proxy", return_value=MOCK_PROXY_RETURN) as mock_proxy,
            patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=_exists_on_disk_true),
        ):
            response = self._post_xfer(writer_client, sample_project.slug)

        assert response.status_code == 200
        assert _get_localauth(mock_proxy) is True

    def test_read_only_user_denied(self, no_perm_client, sample_project, fossil_repo_obj):
        """User without read access gets 403."""
        with patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=_exists_on_disk_true):
            response = self._post_xfer(no_perm_client, sample_project.slug)

        assert response.status_code == 403

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

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
    writer = User.objects.create_user(username="writer", password="testpass123")
    team = Team.objects.create(name="Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="writer", password="testpass123")
    return client


# --- Shun List View Tests ---


@pytest.mark.django_db
class TestShunListView:
    def test_list_shunned_as_admin(self, admin_client, sample_project, fossil_repo_obj):
        with patch("fossil.cli.FossilCLI") as mock_cli_cls:
            cli_instance = MagicMock()
            cli_instance.is_available.return_value = True
            cli_instance.shun_list.return_value = ["abc123def456", "789012345678"]
            mock_cli_cls.return_value = cli_instance
            # Patch exists_on_disk
            with patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=lambda: property(lambda self: True)):
                response = admin_client.get(f"/projects/{sample_project.slug}/fossil/admin/shun/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Shunned Artifacts" in content

    def test_list_empty(self, admin_client, sample_project, fossil_repo_obj):
        with patch("fossil.cli.FossilCLI") as mock_cli_cls:
            cli_instance = MagicMock()
            cli_instance.is_available.return_value = True
            cli_instance.shun_list.return_value = []
            mock_cli_cls.return_value = cli_instance
            with patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=lambda: property(lambda self: True)):
                response = admin_client.get(f"/projects/{sample_project.slug}/fossil/admin/shun/")
        assert response.status_code == 200
        assert "No artifacts have been shunned" in response.content.decode()

    def test_list_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj):
        response = writer_client.get(f"/projects/{sample_project.slug}/fossil/admin/shun/")
        assert response.status_code == 403

    def test_list_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/admin/shun/")
        assert response.status_code == 403

    def test_list_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/admin/shun/")
        assert response.status_code == 302  # redirect to login


# --- Shun Artifact View Tests ---


@pytest.mark.django_db
class TestShunArtifactView:
    def test_shun_artifact_success(self, admin_client, sample_project, fossil_repo_obj):
        with patch("fossil.cli.FossilCLI") as mock_cli_cls:
            cli_instance = MagicMock()
            cli_instance.is_available.return_value = True
            cli_instance.shun.return_value = {"success": True, "message": "Artifact shunned"}
            mock_cli_cls.return_value = cli_instance
            with patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=lambda: property(lambda self: True)):
                response = admin_client.post(
                    f"/projects/{sample_project.slug}/fossil/admin/shun/add/",
                    {
                        "artifact_uuid": "a1b2c3d4e5f67890",
                        "confirmation": "a1b2c3d4",
                        "reason": "Leaked secret",
                    },
                )
        assert response.status_code == 302
        assert response.url == f"/projects/{sample_project.slug}/fossil/admin/shun/"

    def test_shun_requires_confirmation(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/admin/shun/add/",
            {
                "artifact_uuid": "a1b2c3d4e5f67890",
                "confirmation": "wrong",
                "reason": "test",
            },
        )
        assert response.status_code == 302  # redirects with error message

    def test_shun_empty_uuid_rejected(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/admin/shun/add/",
            {
                "artifact_uuid": "",
                "confirmation": "",
            },
        )
        assert response.status_code == 302

    def test_shun_invalid_uuid_format(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/admin/shun/add/",
            {
                "artifact_uuid": "not-a-hex-hash!!!",
                "confirmation": "not-a-he",
            },
        )
        assert response.status_code == 302

    def test_shun_get_redirects(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/admin/shun/add/")
        assert response.status_code == 302

    def test_shun_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/admin/shun/add/",
            {
                "artifact_uuid": "a1b2c3d4e5f67890",
                "confirmation": "a1b2c3d4",
            },
        )
        assert response.status_code == 403

    def test_shun_denied_for_anon(self, client, sample_project):
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/admin/shun/add/",
            {
                "artifact_uuid": "a1b2c3d4e5f67890",
                "confirmation": "a1b2c3d4",
            },
        )
        assert response.status_code == 302  # redirect to login

    def test_shun_cli_failure(self, admin_client, sample_project, fossil_repo_obj):
        with patch("fossil.cli.FossilCLI") as mock_cli_cls:
            cli_instance = MagicMock()
            cli_instance.is_available.return_value = True
            cli_instance.shun.return_value = {"success": False, "message": "Unknown artifact"}
            mock_cli_cls.return_value = cli_instance
            with patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=lambda: property(lambda self: True)):
                response = admin_client.post(
                    f"/projects/{sample_project.slug}/fossil/admin/shun/add/",
                    {
                        "artifact_uuid": "a1b2c3d4e5f67890",
                        "confirmation": "a1b2c3d4",
                        "reason": "test",
                    },
                )
        assert response.status_code == 302


# --- CLI Shun Method Tests ---


@pytest.mark.django_db
class TestFossilCLIShun:
    def test_shun_calls_fossil_binary(self):
        from fossil.cli import FossilCLI

        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Artifact shunned\n", stderr="")
            result = cli.shun("/tmp/test.fossil", "abc123def456", reason="test")
        assert result["success"] is True
        assert "Artifact shunned" in result["message"]
        call_args = mock_run.call_args[0][0]
        assert "shun" in call_args
        assert "abc123def456" in call_args

    def test_shun_returns_failure(self):
        from fossil.cli import FossilCLI

        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Not found")
            result = cli.shun("/tmp/test.fossil", "nonexistent")
        assert result["success"] is False
        assert "Not found" in result["message"]

    def test_shun_list_returns_uuids(self):
        from fossil.cli import FossilCLI

        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc123\ndef456\n", stderr="")
            result = cli.shun_list("/tmp/test.fossil")
        assert result == ["abc123", "def456"]

    def test_shun_list_empty(self):
        from fossil.cli import FossilCLI

        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = cli.shun_list("/tmp/test.fossil")
        assert result == []

    def test_shun_list_failure_returns_empty(self):
        from fossil.cli import FossilCLI

        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = cli.shun_list("/tmp/test.fossil")
        assert result == []

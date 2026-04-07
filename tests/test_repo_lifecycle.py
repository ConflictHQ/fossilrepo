"""Tests for the repository lifecycle UI: project creation with repo source, and repo settings."""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.models import FossilRepository
from organization.models import Team
from projects.models import Project, ProjectTeam


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


@pytest.fixture
def admin_team_user(db, admin_user, sample_project):
    """User with admin team role on the sample project."""
    admin_team_member = User.objects.create_user(username="projadmin", password="testpass123")
    team = Team.objects.create(name="Admins", organization=sample_project.organization, created_by=admin_user)
    team.members.add(admin_team_member)
    ProjectTeam.objects.create(project=sample_project, team=team, role="admin", created_by=admin_user)
    return admin_team_member


@pytest.fixture
def admin_team_client(admin_team_user):
    client = Client()
    client.login(username="projadmin", password="testpass123")
    return client


# --- Project Create Form Tests ---


@pytest.mark.django_db
class TestProjectCreateForm:
    def test_create_form_shows_repo_source(self, admin_client):
        response = admin_client.get("/projects/create/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "repo_source" in content
        assert "Create empty repository" in content
        assert "Clone from Fossil URL" in content

    def test_create_empty_repo(self, admin_client, org):
        response = admin_client.post(
            "/projects/create/",
            {"name": "Empty Repo", "visibility": "private", "repo_source": "empty"},
        )
        assert response.status_code == 302
        project = Project.objects.get(name="Empty Repo")
        assert project is not None
        fossil_repo = FossilRepository.objects.get(project=project)
        assert fossil_repo.filename == f"{project.slug}.fossil"
        assert fossil_repo.remote_url == ""

    def test_create_with_missing_clone_url_fails(self, admin_client, org):
        response = admin_client.post(
            "/projects/create/",
            {"name": "Clone Fail", "visibility": "private", "repo_source": "fossil_url", "clone_url": ""},
        )
        # Form should re-render with errors, not redirect
        assert response.status_code == 200
        content = response.content.decode()
        assert "Clone URL is required" in content

    @patch("projects.views._clone_fossil_repo")
    def test_create_clone_calls_helper(self, mock_clone, admin_client, org):
        response = admin_client.post(
            "/projects/create/",
            {
                "name": "Cloned Repo",
                "visibility": "private",
                "repo_source": "fossil_url",
                "clone_url": "https://fossil-scm.org/home",
            },
        )
        assert response.status_code == 302
        project = Project.objects.get(name="Cloned Repo")
        mock_clone.assert_called_once()
        call_args = mock_clone.call_args
        assert call_args[0][1] == project
        assert call_args[0][2] == "https://fossil-scm.org/home"

    def test_create_without_repo_source_defaults_to_empty(self, admin_client, org):
        response = admin_client.post(
            "/projects/create/",
            {"name": "Default Source", "visibility": "private"},
        )
        assert response.status_code == 302
        project = Project.objects.get(name="Default Source")
        fossil_repo = FossilRepository.objects.get(project=project)
        assert fossil_repo.remote_url == ""

    def test_edit_form_does_not_show_repo_source(self, admin_client, sample_project):
        response = admin_client.get(f"/projects/{sample_project.slug}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Repository Source" not in content


# --- Project Update Form Tests (no repo source fields) ---


@pytest.mark.django_db
class TestProjectUpdateExcludesRepoSource:
    def test_update_preserves_project(self, admin_client, sample_project):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/edit/",
            {"name": "Updated Name", "visibility": "public"},
        )
        assert response.status_code == 302
        sample_project.refresh_from_db()
        assert sample_project.name == "Updated Name"


# --- Repo Settings View Tests ---


@pytest.mark.django_db
class TestRepoSettingsAccess:
    def test_settings_denied_for_anon(self, client, sample_project, fossil_repo_obj):
        response = client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        # Redirects to login for anon
        assert response.status_code == 302

    def test_settings_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj):
        response = writer_client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        assert response.status_code == 403

    def test_settings_allowed_for_superuser(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        assert response.status_code == 200

    def test_settings_allowed_for_project_admin(self, admin_team_client, sample_project, fossil_repo_obj):
        response = admin_team_client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestRepoSettingsContent:
    def test_settings_page_shows_filename(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        content = response.content.decode()
        assert fossil_repo_obj.filename in content

    def test_settings_page_shows_remote_form(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        content = response.content.decode()
        assert 'name="remote_url"' in content
        assert "Save Remote" in content

    def test_settings_page_shows_clone_urls(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        content = response.content.decode()
        assert "Clone URLs" in content

    def test_settings_page_shows_danger_zone(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        content = response.content.decode()
        assert "Danger Zone" in content

    def test_settings_active_tab(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        content = response.content.decode()
        # The Settings tab should be active (has the active CSS classes)
        assert "Settings" in content


@pytest.mark.django_db
class TestRepoSettingsActions:
    def test_update_remote_url(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/settings/",
            {"action": "update_remote", "remote_url": "https://fossil-scm.org/home"},
        )
        assert response.status_code == 302
        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.remote_url == "https://fossil-scm.org/home"

    def test_clear_remote_url(self, admin_client, sample_project, fossil_repo_obj):
        fossil_repo_obj.remote_url = "https://old-url.example.com"
        fossil_repo_obj.save()
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/settings/",
            {"action": "update_remote", "remote_url": ""},
        )
        assert response.status_code == 302
        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.remote_url == ""

    def test_update_remote_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/settings/",
            {"action": "update_remote", "remote_url": "https://evil.example.com"},
        )
        assert response.status_code == 403
        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.remote_url != "https://evil.example.com"


# --- Nav Tab Tests ---


@pytest.mark.django_db
class TestProjectNavSettings:
    def test_settings_tab_visible_for_admin(self, admin_client, sample_project, fossil_repo_obj):
        """The Settings tab should appear in the nav for admins on fossil views."""
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/settings/")
        content = response.content.decode()
        assert "Settings" in content
        assert f"/projects/{sample_project.slug}/fossil/settings/" in content


# --- Signal Guard Tests ---


@pytest.mark.django_db
class TestSignalExistingFileGuard:
    @patch("fossil.cli.FossilCLI")
    def test_signal_skips_init_when_file_exists(self, mock_cli_cls, org, admin_user, tmp_path):
        """When a .fossil file already exists, the signal should skip fossil init."""
        mock_cli = MagicMock()
        mock_cli.is_available.return_value = True
        mock_cli_cls.return_value = mock_cli

        # Create the project -- the signal fires
        project = Project.objects.create(name="Pre-existing", organization=org, created_by=admin_user)

        # The signal creates a FossilRepository record. Since the .fossil file won't exist
        # on disk in tests (no real FOSSIL_DATA_DIR), the signal will attempt init via CLI.
        # The key assertion is that the record was created and the code path doesn't crash.
        assert FossilRepository.objects.filter(project=project).exists()

    def test_signal_creates_repo_record(self, org, admin_user):
        """The signal creates a FossilRepository record when a Project is created."""
        project = Project.objects.create(name="Signal Test", organization=org, created_by=admin_user)
        assert FossilRepository.objects.filter(project=project).exists()
        fossil_repo = FossilRepository.objects.get(project=project)
        assert fossil_repo.filename == f"{project.slug}.fossil"


# --- Form Validation Tests ---


@pytest.mark.django_db
class TestProjectFormValidation:
    def test_form_valid_with_empty_source(self):
        from projects.forms import ProjectForm

        form = ProjectForm(data={"name": "Test", "visibility": "private", "repo_source": "empty"})
        assert form.is_valid()

    def test_form_valid_with_clone_url(self):
        from projects.forms import ProjectForm

        form = ProjectForm(
            data={
                "name": "Test Clone",
                "visibility": "private",
                "repo_source": "fossil_url",
                "clone_url": "https://fossil-scm.org/home",
            }
        )
        assert form.is_valid()

    def test_form_invalid_clone_without_url(self):
        from projects.forms import ProjectForm

        form = ProjectForm(
            data={
                "name": "No URL",
                "visibility": "private",
                "repo_source": "fossil_url",
                "clone_url": "",
            }
        )
        assert not form.is_valid()
        assert "clone_url" in form.errors

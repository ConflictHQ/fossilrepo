"""Tests for Git mirror multi-remote sync UI views."""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.models import FossilRepository
from fossil.sync_models import GitMirror
from organization.models import Team
from projects.models import ProjectTeam

# Reusable patch that makes FossilRepository.exists_on_disk return True
_disk_patch = patch("fossil.models.FossilRepository.exists_on_disk", new_callable=lambda: property(lambda self: True))


def _make_reader_mock(**methods):
    """Create a MagicMock that replaces FossilReader as a class."""
    mock_cls = MagicMock()
    instance = MagicMock()
    mock_cls.return_value = instance
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    for name, val in methods.items():
        getattr(instance, name).return_value = val
    return mock_cls


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def mirror(fossil_repo_obj, admin_user):
    return GitMirror.objects.create(
        repository=fossil_repo_obj,
        git_remote_url="https://github.com/org/repo.git",
        auth_method="token",
        auth_credential="ghp_test123",
        sync_direction="push",
        sync_mode="scheduled",
        sync_schedule="*/15 * * * *",
        git_branch="main",
        fossil_branch="trunk",
        created_by=admin_user,
    )


@pytest.fixture
def second_mirror(fossil_repo_obj, admin_user):
    return GitMirror.objects.create(
        repository=fossil_repo_obj,
        git_remote_url="https://gitlab.com/org/repo.git",
        auth_method="oauth_gitlab",
        sync_direction="both",
        sync_mode="both",
        sync_schedule="0 */6 * * *",
        git_branch="main",
        fossil_branch="trunk",
        created_by=admin_user,
    )


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """User with write access but not admin."""
    writer = User.objects.create_user(username="mirror_writer", password="testpass123")
    team = Team.objects.create(name="Mirror Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="mirror_writer", password="testpass123")
    return client


# --- GitMirror Model Tests ---


@pytest.mark.django_db
class TestGitMirrorModel:
    def test_create_mirror(self, mirror):
        assert mirror.pk is not None
        assert mirror.git_remote_url == "https://github.com/org/repo.git"
        assert mirror.sync_direction == "push"

    def test_str_representation(self, mirror):
        assert "github.com/org/repo.git" in str(mirror)

    def test_soft_delete(self, mirror, admin_user):
        mirror.soft_delete(user=admin_user)
        assert mirror.is_deleted
        assert GitMirror.objects.filter(pk=mirror.pk).count() == 0
        assert GitMirror.all_objects.filter(pk=mirror.pk).count() == 1

    def test_multiple_mirrors_per_repo(self, mirror, second_mirror, fossil_repo_obj):
        mirrors = GitMirror.objects.filter(repository=fossil_repo_obj)
        assert mirrors.count() == 2

    def test_ordering(self, mirror, second_mirror):
        """Mirrors are ordered newest first."""
        mirrors = list(GitMirror.objects.all())
        assert mirrors[0] == second_mirror
        assert mirrors[1] == mirror


# --- Sync Page (sync.html) showing mirrors ---


@pytest.mark.django_db
class TestSyncPageMirrorListing:
    def test_sync_page_shows_mirrors(self, admin_client, sample_project, fossil_repo_obj, mirror, second_mirror):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/sync/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "github.com/org/repo.git" in content
        assert "gitlab.com/org/repo.git" in content
        assert "Git Mirrors" in content

    def test_sync_page_shows_empty_state(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/sync/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "No Git mirrors configured" in content

    def test_sync_page_shows_add_mirror_button(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/sync/")
        assert response.status_code == 200
        assert "Add Mirror" in response.content.decode()

    def test_sync_page_shows_mirror_direction_badges(self, admin_client, sample_project, fossil_repo_obj, mirror, second_mirror):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/sync/")
        content = response.content.decode()
        # Check direction labels rendered
        assert "Push" in content
        assert "Bidirectional" in content

    def test_sync_page_shows_edit_delete_links(self, admin_client, sample_project, fossil_repo_obj, mirror):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/sync/")
        content = response.content.decode()
        assert "Edit" in content
        assert "Delete" in content
        assert "Run Now" in content

    def test_sync_page_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/sync/")
        assert response.status_code == 302  # redirect to login


# --- Git Mirror Config View (Add) ---


@pytest.mark.django_db
class TestGitMirrorAddView:
    def test_get_add_form(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/sync/git/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Add Git Mirror" in content
        assert "Quick Connect" in content

    def test_create_mirror(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.post(
                f"/projects/{sample_project.slug}/fossil/sync/git/",
                {
                    "action": "create",
                    "git_remote_url": "https://github.com/test/new-repo.git",
                    "auth_method": "token",
                    "auth_credential": "ghp_newtoken",
                    "sync_direction": "push",
                    "sync_mode": "scheduled",
                    "sync_schedule": "*/30 * * * *",
                    "git_branch": "main",
                    "fossil_branch": "trunk",
                },
            )
        assert response.status_code == 302
        mirror = GitMirror.objects.get(git_remote_url="https://github.com/test/new-repo.git")
        assert mirror.sync_direction == "push"
        assert mirror.sync_schedule == "*/30 * * * *"
        assert mirror.fossil_branch == "trunk"

    def test_create_mirror_with_sync_tickets(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.post(
                f"/projects/{sample_project.slug}/fossil/sync/git/",
                {
                    "action": "create",
                    "git_remote_url": "https://github.com/test/tickets-repo.git",
                    "auth_method": "token",
                    "sync_direction": "push",
                    "sync_mode": "scheduled",
                    "sync_schedule": "*/15 * * * *",
                    "git_branch": "main",
                    "fossil_branch": "trunk",
                    "sync_tickets": "on",
                    "sync_wiki": "on",
                },
            )
        assert response.status_code == 302
        mirror = GitMirror.objects.get(git_remote_url="https://github.com/test/tickets-repo.git")
        assert mirror.sync_tickets is True
        assert mirror.sync_wiki is True

    def test_create_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/sync/git/",
            {"action": "create", "git_remote_url": "https://evil.com/repo.git"},
        )
        assert response.status_code == 403

    def test_create_denied_for_anon(self, client, sample_project):
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/sync/git/",
            {"action": "create", "git_remote_url": "https://example.com/repo.git"},
        )
        assert response.status_code == 302  # redirect to login


# --- Git Mirror Config View (Edit) ---


@pytest.mark.django_db
class TestGitMirrorEditView:
    def test_get_edit_form(self, admin_client, sample_project, fossil_repo_obj, mirror):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Edit Git Mirror" in content
        assert "github.com/org/repo.git" in content
        # Should NOT show quick connect section when editing
        assert "Quick Connect" not in content

    def test_edit_mirror(self, admin_client, sample_project, fossil_repo_obj, mirror):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.post(
                f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/edit/",
                {
                    "action": "update",
                    "git_remote_url": "https://github.com/org/updated-repo.git",
                    "auth_method": "ssh",
                    "sync_direction": "both",
                    "sync_mode": "both",
                    "sync_schedule": "0 */2 * * *",
                    "git_branch": "develop",
                    "fossil_branch": "trunk",
                },
            )
        assert response.status_code == 302
        mirror.refresh_from_db()
        assert mirror.git_remote_url == "https://github.com/org/updated-repo.git"
        assert mirror.auth_method == "ssh"
        assert mirror.sync_direction == "both"
        assert mirror.sync_schedule == "0 */2 * * *"
        assert mirror.git_branch == "develop"

    def test_edit_preserves_credential_when_blank(self, admin_client, sample_project, fossil_repo_obj, mirror):
        """Editing without providing a new credential should keep the old one."""
        old_credential = mirror.auth_credential
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.post(
                f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/edit/",
                {
                    "action": "update",
                    "git_remote_url": "https://github.com/org/repo.git",
                    "auth_method": "token",
                    "auth_credential": "",
                    "sync_direction": "push",
                    "sync_mode": "scheduled",
                    "sync_schedule": "*/15 * * * *",
                    "git_branch": "main",
                    "fossil_branch": "trunk",
                },
            )
        assert response.status_code == 302
        mirror.refresh_from_db()
        assert mirror.auth_credential == old_credential

    def test_edit_nonexistent_mirror(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/sync/git/99999/edit/")
        assert response.status_code == 404

    def test_edit_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj, mirror):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/edit/",
            {"action": "update", "git_remote_url": "https://evil.com/repo.git"},
        )
        assert response.status_code == 403


# --- Git Mirror Delete View ---


@pytest.mark.django_db
class TestGitMirrorDeleteView:
    def test_get_delete_confirmation(self, admin_client, sample_project, fossil_repo_obj, mirror):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/delete/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Delete Git Mirror" in content
        assert "github.com/org/repo.git" in content

    def test_delete_mirror(self, admin_client, sample_project, fossil_repo_obj, mirror):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.post(f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/delete/")
        assert response.status_code == 302
        mirror.refresh_from_db()
        assert mirror.is_deleted

    def test_delete_removes_from_active_queries(self, admin_client, sample_project, fossil_repo_obj, mirror):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            admin_client.post(f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/delete/")
        assert GitMirror.objects.filter(pk=mirror.pk).count() == 0
        assert GitMirror.all_objects.filter(pk=mirror.pk).count() == 1

    def test_delete_nonexistent_mirror(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock()
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.post(f"/projects/{sample_project.slug}/fossil/sync/git/99999/delete/")
        assert response.status_code == 404

    def test_delete_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj, mirror):
        response = writer_client.post(f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/delete/")
        assert response.status_code == 403

    def test_delete_denied_for_anon(self, client, sample_project, mirror):
        response = client.post(f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/delete/")
        assert response.status_code == 302  # redirect to login


# --- Git Mirror Run View ---


@pytest.mark.django_db
class TestGitMirrorRunView:
    def test_run_mirror(self, admin_client, sample_project, fossil_repo_obj, mirror):
        mock_reader = _make_reader_mock()
        mock_task = MagicMock()
        with _disk_patch, patch("fossil.views.FossilReader", mock_reader), patch("fossil.tasks.run_git_sync", mock_task):
            response = admin_client.post(f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/run/")
        assert response.status_code == 302
        mock_task.delay.assert_called_once_with(mirror.pk)

    def test_run_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj, mirror):
        response = writer_client.post(f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/run/")
        assert response.status_code == 403

    def test_run_denied_for_anon(self, client, sample_project, mirror):
        response = client.post(f"/projects/{sample_project.slug}/fossil/sync/git/{mirror.pk}/run/")
        assert response.status_code == 302  # redirect to login

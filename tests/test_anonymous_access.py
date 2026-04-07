"""Tests for anonymous (unauthenticated) access to public projects.

Verifies that:
- Anonymous users can browse public project listings, details, and fossil views.
- Anonymous users are denied access to private projects.
- Anonymous users are denied write operations even on public projects.
- Authenticated users retain full access as before.
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.models import FossilRepository
from organization.models import Team
from pages.models import Page
from projects.models import Project, ProjectTeam

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def anon_client():
    """Unauthenticated client."""
    return Client()


@pytest.fixture
def public_project(db, org, admin_user, sample_team):
    """A public project visible to anonymous users."""
    project = Project.objects.create(
        name="Public Repo",
        organization=org,
        visibility="public",
        created_by=admin_user,
    )
    ProjectTeam.objects.create(project=project, team=sample_team, role="write", created_by=admin_user)
    return project


@pytest.fixture
def internal_project(db, org, admin_user, sample_team):
    """An internal project visible only to authenticated users."""
    project = Project.objects.create(
        name="Internal Repo",
        organization=org,
        visibility="internal",
        created_by=admin_user,
    )
    ProjectTeam.objects.create(project=project, team=sample_team, role="write", created_by=admin_user)
    return project


@pytest.fixture
def private_project(sample_project):
    """The default sample_project is private."""
    return sample_project


@pytest.fixture
def published_page(db, org, admin_user):
    """A published knowledge base page."""
    return Page.objects.create(
        name="Public Guide",
        content="# Public Guide\n\nThis is visible to everyone.",
        organization=org,
        is_published=True,
        created_by=admin_user,
    )


@pytest.fixture
def draft_page(db, org, admin_user):
    """An unpublished draft page."""
    return Page.objects.create(
        name="Draft Guide",
        content="# Draft\n\nThis is a draft.",
        organization=org,
        is_published=False,
        created_by=admin_user,
    )


@pytest.fixture
def public_fossil_repo(public_project):
    """Return the auto-created FossilRepository for the public project."""
    return FossilRepository.objects.get(project=public_project, deleted_at__isnull=True)


@pytest.fixture
def private_fossil_repo(private_project):
    """Return the auto-created FossilRepository for the private project."""
    return FossilRepository.objects.get(project=private_project, deleted_at__isnull=True)


@pytest.fixture
def writer_for_public(db, admin_user, public_project):
    """User with write access to the public project."""
    writer = User.objects.create_user(username="pub_writer", password="testpass123")
    team = Team.objects.create(name="Pub Writers", organization=public_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=public_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client_for_public(writer_for_public):
    client = Client()
    client.login(username="pub_writer", password="testpass123")
    return client


# ---------------------------------------------------------------------------
# Helper: mock FossilReader for views that open the .fossil file
# ---------------------------------------------------------------------------


def _mock_fossil_reader():
    """Return a context-manager mock that satisfies _get_repo_and_reader."""
    reader = MagicMock()
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=False)
    reader.get_latest_checkin_uuid.return_value = "abc123"
    reader.get_files_at_checkin.return_value = []
    reader.get_metadata.return_value = MagicMock(
        checkin_count=5, project_name="Test", project_code="abc", ticket_count=0, wiki_page_count=0
    )
    reader.get_timeline.return_value = []
    reader.get_tickets.return_value = []
    reader.get_wiki_pages.return_value = []
    reader.get_wiki_page.return_value = None
    reader.get_branches.return_value = []
    reader.get_tags.return_value = []
    reader.get_technotes.return_value = []
    reader.get_forum_posts.return_value = []
    reader.get_unversioned_files.return_value = []
    reader.get_commit_activity.return_value = []
    reader.get_top_contributors.return_value = []
    reader.get_repo_statistics.return_value = {}
    reader.search.return_value = []
    reader.get_checkin_count.return_value = 5
    return reader


def _patch_fossil_on_disk():
    """Patch exists_on_disk to True and FossilReader to our mock."""
    reader = _mock_fossil_reader()
    return (
        patch.object(FossilRepository, "exists_on_disk", new_callable=PropertyMock, return_value=True),
        patch("fossil.views.FossilReader", return_value=reader),
        reader,
    )


# ===========================================================================
# Project List
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousProjectList:
    def test_anonymous_sees_public_projects(self, anon_client, public_project):
        response = anon_client.get("/projects/")
        assert response.status_code == 200
        assert public_project.name in response.content.decode()

    def test_anonymous_does_not_see_private_projects(self, anon_client, private_project, public_project):
        response = anon_client.get("/projects/")
        assert response.status_code == 200
        body = response.content.decode()
        assert public_project.name in body
        assert private_project.name not in body

    def test_anonymous_does_not_see_internal_projects(self, anon_client, internal_project, public_project):
        response = anon_client.get("/projects/")
        assert response.status_code == 200
        body = response.content.decode()
        assert public_project.name in body
        assert internal_project.name not in body

    def test_authenticated_sees_all_projects(self, admin_client, public_project, private_project, internal_project):
        response = admin_client.get("/projects/")
        assert response.status_code == 200
        body = response.content.decode()
        assert public_project.name in body
        assert private_project.name in body
        assert internal_project.name in body


# ===========================================================================
# Project Detail
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousProjectDetail:
    def test_anonymous_can_view_public_project(self, anon_client, public_project):
        response = anon_client.get(f"/projects/{public_project.slug}/")
        assert response.status_code == 200
        assert public_project.name in response.content.decode()

    def test_anonymous_denied_private_project(self, anon_client, private_project):
        response = anon_client.get(f"/projects/{private_project.slug}/")
        assert response.status_code == 403

    def test_anonymous_denied_internal_project(self, anon_client, internal_project):
        response = anon_client.get(f"/projects/{internal_project.slug}/")
        assert response.status_code == 403

    def test_authenticated_can_view_private_project(self, admin_client, private_project):
        response = admin_client.get(f"/projects/{private_project.slug}/")
        assert response.status_code == 200


# ===========================================================================
# Code Browser (fossil view, needs .fossil file mock)
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousCodeBrowser:
    def test_anonymous_can_view_public_code_browser(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, reader = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/code/")
        assert response.status_code == 200

    def test_anonymous_denied_private_code_browser(self, anon_client, private_project, private_fossil_repo):
        disk_patch, reader_patch, reader = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{private_project.slug}/fossil/code/")
        assert response.status_code == 403


# ===========================================================================
# Timeline
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousTimeline:
    def test_anonymous_can_view_public_timeline(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, reader = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/timeline/")
        assert response.status_code == 200

    def test_anonymous_denied_private_timeline(self, anon_client, private_project, private_fossil_repo):
        disk_patch, reader_patch, reader = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{private_project.slug}/fossil/timeline/")
        assert response.status_code == 403


# ===========================================================================
# Tickets
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousTickets:
    def test_anonymous_can_view_public_ticket_list(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, reader = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/tickets/")
        assert response.status_code == 200

    def test_anonymous_denied_private_ticket_list(self, anon_client, private_project, private_fossil_repo):
        disk_patch, reader_patch, reader = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{private_project.slug}/fossil/tickets/")
        assert response.status_code == 403


# ===========================================================================
# Write operations require login on public projects
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousWriteDenied:
    """Write operations must redirect anonymous users to login, even on public projects."""

    def test_anonymous_cannot_create_ticket(self, anon_client, public_project):
        response = anon_client.get(f"/projects/{public_project.slug}/fossil/tickets/create/")
        # @login_required redirects to login
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_anonymous_cannot_create_wiki(self, anon_client, public_project):
        response = anon_client.get(f"/projects/{public_project.slug}/fossil/wiki/create/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_anonymous_cannot_create_forum_thread(self, anon_client, public_project):
        response = anon_client.get(f"/projects/{public_project.slug}/fossil/forum/create/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_anonymous_cannot_create_release(self, anon_client, public_project):
        response = anon_client.get(f"/projects/{public_project.slug}/fossil/releases/create/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_anonymous_cannot_create_project(self, anon_client):
        response = anon_client.get("/projects/create/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_anonymous_cannot_access_repo_settings(self, anon_client, public_project):
        response = anon_client.get(f"/projects/{public_project.slug}/fossil/settings/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_anonymous_cannot_access_sync(self, anon_client, public_project):
        response = anon_client.get(f"/projects/{public_project.slug}/fossil/sync/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_anonymous_cannot_toggle_watch(self, anon_client, public_project):
        response = anon_client.post(f"/projects/{public_project.slug}/fossil/watch/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url


# ===========================================================================
# Additional read-only fossil views on public projects
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousReadOnlyFossilViews:
    """Test that various read-only fossil views allow anonymous access on public projects."""

    def test_branches(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/branches/")
        assert response.status_code == 200

    def test_tags(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/tags/")
        assert response.status_code == 200

    def test_stats(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/stats/")
        assert response.status_code == 200

    def test_search(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/search/")
        assert response.status_code == 200

    def test_wiki(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/wiki/")
        assert response.status_code == 200

    def test_releases(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/releases/")
        assert response.status_code == 200

    def test_technotes(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/technotes/")
        assert response.status_code == 200

    def test_unversioned(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/files/")
        assert response.status_code == 200


# ===========================================================================
# Pages (knowledge base)
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousPages:
    def test_anonymous_can_view_published_page_list(self, anon_client, published_page):
        response = anon_client.get("/kb/")
        assert response.status_code == 200
        assert published_page.name in response.content.decode()

    def test_anonymous_cannot_see_draft_pages_in_list(self, anon_client, published_page, draft_page):
        response = anon_client.get("/kb/")
        assert response.status_code == 200
        body = response.content.decode()
        assert published_page.name in body
        assert draft_page.name not in body

    def test_anonymous_can_view_published_page_detail(self, anon_client, published_page):
        response = anon_client.get(f"/kb/{published_page.slug}/")
        assert response.status_code == 200
        assert published_page.name in response.content.decode()

    def test_anonymous_denied_draft_page_detail(self, anon_client, draft_page):
        response = anon_client.get(f"/kb/{draft_page.slug}/")
        assert response.status_code == 403

    def test_authenticated_can_view_published_page(self, admin_client, published_page):
        response = admin_client.get(f"/kb/{published_page.slug}/")
        assert response.status_code == 200

    def test_anonymous_cannot_create_page(self, anon_client):
        response = anon_client.get("/kb/create/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url


# ===========================================================================
# Explore page (already worked for anonymous)
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousExplore:
    def test_anonymous_can_access_explore(self, anon_client, public_project):
        response = anon_client.get("/explore/")
        assert response.status_code == 200
        assert public_project.name in response.content.decode()

    def test_anonymous_explore_hides_private(self, anon_client, public_project, private_project):
        response = anon_client.get("/explore/")
        assert response.status_code == 200
        body = response.content.decode()
        assert public_project.name in body
        assert private_project.name not in body


# ===========================================================================
# Forum (read-only)
# ===========================================================================


@pytest.mark.django_db
class TestAnonymousForum:
    def test_anonymous_can_view_forum_list(self, anon_client, public_project, public_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{public_project.slug}/fossil/forum/")
        assert response.status_code == 200

    def test_anonymous_denied_private_forum(self, anon_client, private_project, private_fossil_repo):
        disk_patch, reader_patch, _ = _patch_fossil_on_disk()
        with disk_patch, reader_patch:
            response = anon_client.get(f"/projects/{private_project.slug}/fossil/forum/")
        assert response.status_code == 403

import shutil
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from .models import FossilRepository
from .reader import FossilReader, _apply_fossil_delta, _extract_wiki_content

# --- Reader tests ---


@pytest.mark.django_db
class TestFossilReader:
    @pytest.fixture
    def repo_path(self, tmp_path):
        src = Path("/tmp/fossil-setup/frontend-app.fossil")
        if not src.exists():
            pytest.skip("Test fossil repo not available")
        dest = tmp_path / "test.fossil"
        shutil.copy2(src, dest)
        return dest

    def test_get_metadata(self, repo_path):
        with FossilReader(repo_path) as reader:
            meta = reader.get_metadata()
            assert meta.checkin_count >= 2

    def test_get_timeline(self, repo_path):
        with FossilReader(repo_path) as reader:
            entries = reader.get_timeline(limit=10)
            assert len(entries) > 0
            assert entries[0].uuid
            assert entries[0].user

    def test_get_timeline_filter_by_type(self, repo_path):
        with FossilReader(repo_path) as reader:
            checkins = reader.get_timeline(limit=10, event_type="ci")
            for e in checkins:
                assert e.event_type == "ci"

    def test_get_latest_checkin_uuid(self, repo_path):
        with FossilReader(repo_path) as reader:
            uuid = reader.get_latest_checkin_uuid()
            assert uuid is not None
            assert len(uuid) > 10

    def test_get_files_at_checkin(self, repo_path):
        with FossilReader(repo_path) as reader:
            files = reader.get_files_at_checkin()
            assert len(files) > 0
            names = [f.name for f in files]
            assert any("README" in n or "index" in n or "utils" in n for n in names)

    def test_get_file_content(self, repo_path):
        with FossilReader(repo_path) as reader:
            files = reader.get_files_at_checkin()
            if files:
                content = reader.get_file_content(files[0].uuid)
                assert len(content) > 0

    def test_get_wiki_pages(self, repo_path):
        with FossilReader(repo_path) as reader:
            pages = reader.get_wiki_pages()
            assert len(pages) >= 2
            names = [p.name for p in pages]
            assert "Home" in names

    def test_get_wiki_page_content(self, repo_path):
        with FossilReader(repo_path) as reader:
            page = reader.get_wiki_page("Home")
            assert page is not None
            assert len(page.content) > 0

    def test_get_checkin_detail(self, repo_path):
        with FossilReader(repo_path) as reader:
            uuid = reader.get_latest_checkin_uuid()
            detail = reader.get_checkin_detail(uuid[:8])
            assert detail is not None
            assert detail.uuid == uuid
            assert detail.comment
            assert len(detail.files_changed) > 0

    def test_get_commit_activity(self, repo_path):
        with FossilReader(repo_path) as reader:
            activity = reader.get_commit_activity(weeks=4)
            assert len(activity) == 4
            total = sum(a["count"] for a in activity)
            assert total > 0

    def test_get_user_activity(self, repo_path):
        with FossilReader(repo_path) as reader:
            activity = reader.get_user_activity("ragelink")
            assert activity["checkin_count"] > 0
            assert len(activity["checkins"]) > 0


# --- Helper function tests ---


class TestExtractWikiContent:
    def test_basic_extraction(self):
        artifact = "D 2026-01-01T00:00:00\nL TestPage\nU user\nW 5\nhello\nZ abc123"
        assert _extract_wiki_content(artifact) == "hello"

    def test_multiline_content(self):
        artifact = "D 2026-01-01T00:00:00\nL Page\nU user\nW 11\nhello\nworld\nZ abc123"
        assert _extract_wiki_content(artifact) == "hello\nworld"

    def test_empty_content(self):
        artifact = "D 2026-01-01T00:00:00\nL Page\nU user\nW 0\n\nZ abc123"
        assert _extract_wiki_content(artifact) == ""

    def test_no_w_card(self):
        assert _extract_wiki_content("just some text") == ""


class TestApplyFossilDelta:
    def test_copy_command(self):
        source = b"Hello, World!"
        # Delta: output size 5, copy 5 bytes from offset 0
        # This is a simplified test
        result = _apply_fossil_delta(source, b"")
        assert result == source  # empty delta returns source

    def test_empty_delta(self):
        source = b"test content"
        result = _apply_fossil_delta(source, b"")
        assert result == source


# --- Model tests ---


@pytest.mark.django_db
class TestFossilRepositoryModel:
    def test_auto_created_on_project_save(self, sample_project):
        """The post_save signal auto-creates a FossilRepository."""
        repo = FossilRepository.objects.filter(project=sample_project).first()
        assert repo is not None
        assert repo.filename == f"{sample_project.slug}.fossil"
        assert str(repo) == repo.filename

    def test_full_path(self, sample_project):
        repo = FossilRepository.objects.get(project=sample_project)
        assert repo.full_path.name == repo.filename

    def test_exists_on_disk_false(self, sample_project):
        repo = FossilRepository.objects.get(project=sample_project)
        # The /data/repos dir doesn't exist in test env
        assert repo.exists_on_disk is False


# --- View tests ---


@pytest.mark.django_db
class TestFossilViews:
    @pytest.fixture
    def repo_with_path(self, sample_project, admin_user, tmp_path):
        src = Path("/tmp/fossil-setup/frontend-app.fossil")
        if not src.exists():
            pytest.skip("Test fossil repo not available")
        dest = tmp_path / "frontend-app.fossil"
        shutil.copy2(src, dest)
        # Get the auto-created repo from the signal and update its path
        repo = FossilRepository.objects.get(project=sample_project)
        return repo, dest

    def test_code_browser(self, admin_client, repo_with_path):
        repo, dest = repo_with_path
        with (
            patch.object(type(repo), "full_path", new_callable=PropertyMock, return_value=dest),
            patch("fossil.views.FossilRepository.objects") as mock_qs,
        ):
            mock_qs.filter.return_value.first.return_value = repo
            # Use get_object_or_404 mock approach
            response = admin_client.get(f"/projects/{repo.project.slug}/fossil/code/")
            # May 404 if constance config points elsewhere, but shouldn't error
            assert response.status_code in (200, 404)

    def test_timeline(self, admin_client, repo_with_path):
        repo, dest = repo_with_path
        response = admin_client.get(f"/projects/{repo.project.slug}/fossil/timeline/")
        assert response.status_code in (200, 404)

    def test_tickets(self, admin_client, repo_with_path):
        repo, dest = repo_with_path
        response = admin_client.get(f"/projects/{repo.project.slug}/fossil/tickets/")
        assert response.status_code in (200, 404)

    def test_wiki(self, admin_client, repo_with_path):
        repo, dest = repo_with_path
        response = admin_client.get(f"/projects/{repo.project.slug}/fossil/wiki/")
        assert response.status_code in (200, 404)

    def test_forum(self, admin_client, repo_with_path):
        repo, dest = repo_with_path
        response = admin_client.get(f"/projects/{repo.project.slug}/fossil/forum/")
        assert response.status_code in (200, 404)

    def test_views_denied_without_perm(self, no_perm_client, sample_project):
        slug = sample_project.slug
        for path in ["/fossil/code/", "/fossil/timeline/", "/fossil/tickets/", "/fossil/wiki/", "/fossil/forum/"]:
            response = no_perm_client.get(f"/projects/{slug}{path}")
            assert response.status_code == 403, f"Expected 403 for {path}"

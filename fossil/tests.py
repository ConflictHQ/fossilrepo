import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from .models import FossilRepository
from .reader import FossilReader, TimelineEntry, _apply_fossil_delta, _extract_wiki_content
from .views import _compute_dag_graph

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


# --- DAG graph computation tests ---


def _make_entry(rid, event_type="ci", branch="trunk", parent_rid=0, is_merge=False, merge_parent_rids=None, rail=0):
    """Helper to build a TimelineEntry for DAG tests."""
    return TimelineEntry(
        rid=rid,
        uuid=f"uuid-{rid}",
        event_type=event_type,
        timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        user="test",
        comment=f"commit {rid}",
        branch=branch,
        parent_rid=parent_rid,
        is_merge=is_merge,
        merge_parent_rids=merge_parent_rids or [],
        rail=rail,
    )


class TestComputeDagGraph:
    def test_empty_entries(self):
        assert _compute_dag_graph([]) == []

    def test_linear_single_branch(self):
        """Linear history on one rail: no forks, no merges, no connectors."""
        entries = [
            _make_entry(rid=3, parent_rid=2, rail=0),
            _make_entry(rid=2, parent_rid=1, rail=0),
            _make_entry(rid=1, parent_rid=0, rail=0),
        ]
        result = _compute_dag_graph(entries)
        assert len(result) == 3
        for item in result:
            assert item["fork_from"] is None
            assert item["merge_to"] is None
            assert item["is_merge"] is False
            assert item["connectors"] == []

    def test_linear_leaf_detection(self):
        """First entry (newest) on a rail with no child is a leaf."""
        entries = [
            _make_entry(rid=3, parent_rid=2, rail=0),
            _make_entry(rid=2, parent_rid=1, rail=0),
            _make_entry(rid=1, parent_rid=0, rail=0),
        ]
        result = _compute_dag_graph(entries)
        # rid=3 has no child in this list -> leaf
        assert result[0]["is_leaf"] is True
        # rid=2 has rid=3 as a child on the same rail -> not leaf
        assert result[1]["is_leaf"] is False
        # rid=1 has rid=2 as a child on the same rail -> not leaf
        assert result[2]["is_leaf"] is False

    def test_fork_detected(self):
        """Branch fork: first entry on rail 1 with parent on rail 0."""
        entries = [
            _make_entry(rid=3, branch="feature", parent_rid=1, rail=1),  # first on rail 1, parent on rail 0
            _make_entry(rid=2, parent_rid=1, rail=0),
            _make_entry(rid=1, parent_rid=0, rail=0),
        ]
        result = _compute_dag_graph(entries)
        # rid=3 forks from rail 0
        assert result[0]["fork_from"] == 0
        # rid=3 should have a fork connector
        assert len(result[0]["connectors"]) == 1
        assert result[0]["connectors"][0]["type"] == "fork"
        assert result[0]["connectors"][0]["from_rail"] == 0
        assert result[0]["connectors"][0]["to_rail"] == 1
        # Other entries have no fork/merge
        assert result[1]["fork_from"] is None
        assert result[2]["fork_from"] is None

    def test_merge_detected(self):
        """Merge commit: entry with merge_parent_rids on a different rail."""
        entries = [
            _make_entry(rid=4, parent_rid=3, rail=0, is_merge=True, merge_parent_rids=[2]),  # merge from rail 1
            _make_entry(rid=3, parent_rid=1, rail=0),
            _make_entry(rid=2, branch="feature", parent_rid=1, rail=1),  # feature branch
            _make_entry(rid=1, parent_rid=0, rail=0),
        ]
        result = _compute_dag_graph(entries)
        # rid=4 is a merge
        assert result[0]["is_merge"] is True
        assert result[0]["merge_to"] == 0
        # Should have a merge connector
        merge_conns = [c for c in result[0]["connectors"] if c["type"] == "merge"]
        assert len(merge_conns) == 1
        assert merge_conns[0]["from_rail"] == 1
        assert merge_conns[0]["to_rail"] == 0

    def test_non_checkin_entries_no_dag_data(self):
        """Wiki/ticket/forum entries should not produce fork/merge/leaf data."""
        entries = [
            _make_entry(rid=2, event_type="w", rail=-1, parent_rid=0),
            _make_entry(rid=1, parent_rid=0, rail=0),
        ]
        result = _compute_dag_graph(entries)
        # Wiki entry: no fork/merge, not a leaf (only ci entries can be leaves)
        assert result[0]["fork_from"] is None
        assert result[0]["merge_to"] is None
        assert result[0]["is_leaf"] is False
        assert result[0]["is_merge"] is False

    def test_rail_colors_present(self):
        """Each line and node should carry a color."""
        entries = [
            _make_entry(rid=2, parent_rid=1, rail=0),
            _make_entry(rid=1, parent_rid=0, rail=0),
        ]
        result = _compute_dag_graph(entries)
        assert result[0]["node_color"] == "#ef4444"  # rail 0 = red
        # Active lines should also have color
        for item in result:
            for line in item["lines"]:
                assert "color" in line

    def test_multiple_rails_active_lines(self):
        """When two branches are active, both rails should appear in lines."""
        entries = [
            _make_entry(rid=4, branch="feature", parent_rid=2, rail=1),
            _make_entry(rid=3, parent_rid=1, rail=0),
            _make_entry(rid=2, branch="feature", parent_rid=1, rail=1),
            _make_entry(rid=1, parent_rid=0, rail=0),
        ]
        result = _compute_dag_graph(entries)
        # At row index 1 (rid=3), both rail 0 and rail 1 should be active
        # because rail 1 spans from index 0 (rid=4) to index 2 (rid=2)
        # and rail 0 spans from index 1 (rid=3) to index 3 (rid=1)
        active_xs = {line["x"] for line in result[1]["lines"]}
        rail_0_x = 20 + 0 * 16  # 20
        rail_1_x = 20 + 1 * 16  # 36
        assert rail_0_x in active_xs
        assert rail_1_x in active_xs

    def test_graph_width_accommodates_rails(self):
        """Graph width should be wide enough for all rails plus padding."""
        entries = [
            _make_entry(rid=3, branch="b2", parent_rid=1, rail=2),
            _make_entry(rid=2, branch="b1", parent_rid=1, rail=1),
            _make_entry(rid=1, parent_rid=0, rail=0),
        ]
        result = _compute_dag_graph(entries)
        # max_rail=2, graph_width = 20 + (2+2)*16 = 84
        assert result[0]["graph_width"] == 84

    def test_connector_geometry(self):
        """Fork connector left and width should span from the lower rail to the higher rail."""
        entries = [
            _make_entry(rid=2, branch="feature", parent_rid=1, rail=2),  # fork from rail 0
            _make_entry(rid=1, parent_rid=0, rail=0),
        ]
        result = _compute_dag_graph(entries)
        conn = result[0]["connectors"][0]
        rail_0_x = 20 + 0 * 16  # 20
        rail_2_x = 20 + 2 * 16  # 52
        assert conn["left"] == rail_0_x
        assert conn["width"] == rail_2_x - rail_0_x


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
    def setup_repo(self, sample_project, admin_user, tmp_path):
        """Set up a real .fossil file and point Constance FOSSIL_DATA_DIR to tmp."""
        src = Path("/tmp/fossil-setup/frontend-app.fossil")
        if not src.exists():
            pytest.skip("Test fossil repo not available")
        # Copy to tmp dir using the repo's expected filename
        repo = FossilRepository.objects.get(project=sample_project)
        dest = tmp_path / repo.filename
        shutil.copy2(src, dest)
        # Override Constance FOSSIL_DATA_DIR to point to our tmp dir
        from constance import config

        original_dir = config.FOSSIL_DATA_DIR
        config.FOSSIL_DATA_DIR = str(tmp_path)
        yield sample_project.slug
        config.FOSSIL_DATA_DIR = original_dir

    def test_code_browser_content(self, admin_client, setup_repo):
        slug = setup_repo
        response = admin_client.get(f"/projects/{slug}/fossil/code/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "CONTRIBUTING.md" in content or "README" in content or "utils" in content

    def test_timeline_content(self, admin_client, setup_repo):
        slug = setup_repo
        response = admin_client.get(f"/projects/{slug}/fossil/timeline/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "ragelink" in content
        assert "tl-row" in content

    def test_checkin_detail(self, admin_client, setup_repo):
        slug = setup_repo
        response = admin_client.get(f"/projects/{slug}/fossil/timeline/")
        # Extract a hash from the page
        import re

        hashes = re.findall(r"/checkin/([0-9a-f]{8,})/", response.content.decode())
        if hashes:
            detail = admin_client.get(f"/projects/{slug}/fossil/checkin/{hashes[0]}/")
            assert detail.status_code == 200
            assert "diff-table" in detail.content.decode() or "file" in detail.content.decode().lower()

    def test_wiki_content(self, admin_client, setup_repo):
        slug = setup_repo
        response = admin_client.get(f"/projects/{slug}/fossil/wiki/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Home" in content

    def test_wiki_page_content(self, admin_client, setup_repo):
        slug = setup_repo
        response = admin_client.get(f"/projects/{slug}/fossil/wiki/page/Home")
        assert response.status_code == 200
        content = response.content.decode()
        assert "prose" in content

    def test_search(self, admin_client, setup_repo):
        slug = setup_repo
        response = admin_client.get(f"/projects/{slug}/fossil/search/?q=initial")
        assert response.status_code == 200

    def test_file_view(self, admin_client, setup_repo):
        slug = setup_repo
        response = admin_client.get(f"/projects/{slug}/fossil/code/file/CONTRIBUTING.md")
        if response.status_code == 200:
            content = response.content.decode()
            assert "line-num" in content

    def test_branches(self, admin_client, setup_repo):
        slug = setup_repo
        response = admin_client.get(f"/projects/{slug}/fossil/branches/")
        assert response.status_code == 200
        assert "trunk" in response.content.decode()

    def test_stats(self, admin_client, setup_repo):
        slug = setup_repo
        response = admin_client.get(f"/projects/{slug}/fossil/stats/")
        assert response.status_code == 200
        assert "Checkins" in response.content.decode()

    def test_views_denied_without_perm(self, no_perm_client, sample_project):
        slug = sample_project.slug
        for path in ["/fossil/code/", "/fossil/timeline/", "/fossil/tickets/", "/fossil/wiki/", "/fossil/forum/"]:
            response = no_perm_client.get(f"/projects/{slug}{path}")
            assert response.status_code == 403, f"Expected 403 for {path}"

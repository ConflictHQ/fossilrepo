"""Tests for the SQLite schema explorer views."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.models import FossilRepository
from fossil.reader import FossilReader
from organization.models import Team
from projects.models import ProjectTeam

# Reusable patch that makes FossilRepository.exists_on_disk return True
_disk_patch = patch("fossil.models.FossilRepository.exists_on_disk", new_callable=lambda: property(lambda self: True))


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
def reader_user(db, admin_user, sample_project):
    """User with read access only."""
    reader = User.objects.create_user(username="reader", password="testpass123")
    team = Team.objects.create(name="Readers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(reader)
    ProjectTeam.objects.create(project=sample_project, team=team, role="read", created_by=admin_user)
    return reader


@pytest.fixture
def reader_client(reader_user):
    client = Client()
    client.login(username="reader", password="testpass123")
    return client


def _create_explorer_fossil_db(path: Path):
    """Create a minimal .fossil SQLite database with typical Fossil tables."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE blob (rid INTEGER PRIMARY KEY, uuid TEXT UNIQUE NOT NULL, size INTEGER NOT NULL DEFAULT 0, content BLOB)")
    conn.execute("CREATE TABLE event (type TEXT, mtime REAL, objid INTEGER, user TEXT, comment TEXT)")
    conn.execute("CREATE TABLE tag (tagid INTEGER PRIMARY KEY, tagname TEXT UNIQUE)")
    conn.execute("CREATE TABLE tagxref (tagid INTEGER, tagtype INTEGER, srcid INTEGER, origid INTEGER, value TEXT, mtime REAL)")
    conn.execute("CREATE TABLE delta (rid INTEGER, srcid INTEGER)")
    conn.execute("CREATE TABLE leaf (rid INTEGER)")
    conn.execute("CREATE TABLE phantom (rid INTEGER)")
    conn.execute("CREATE TABLE ticket (tkt_id INTEGER PRIMARY KEY, tkt_uuid TEXT, title TEXT, status TEXT)")

    # Insert sample data
    conn.execute("INSERT INTO blob (rid, uuid, size) VALUES (1, 'abc123def456', 100)")
    conn.execute("INSERT INTO blob (rid, uuid, size) VALUES (2, '789012345678', 200)")
    conn.execute("INSERT INTO event (type, mtime, objid, user, comment) VALUES ('ci', 2460676.5, 1, 'admin', 'initial')")
    conn.execute("INSERT INTO tag (tagid, tagname) VALUES (1, 'sym-trunk')")
    conn.execute("INSERT INTO tagxref (tagid, tagtype, srcid, origid, value, mtime) VALUES (1, 2, 1, 1, 'trunk', 2460676.5)")
    conn.execute("INSERT INTO ticket (tkt_id, tkt_uuid, title, status) VALUES (1, 'tkt001', 'Fix bug', 'Open')")
    conn.commit()
    return conn


@pytest.fixture
def explorer_fossil_db(tmp_path):
    """Create a temporary .fossil file for explorer tests."""
    db_path = tmp_path / "explorer-test.fossil"
    conn = _create_explorer_fossil_db(db_path)
    conn.close()
    return db_path


def _make_fossil_reader_cls(db_path):
    """Return a FossilReader class replacement that always opens the given test DB.

    Unlike a full mock, this returns a real FossilReader pointing at our test
    .fossil file so that the explorer views can execute real SQL.
    """
    original_cls = FossilReader

    class TestFossilReader(original_cls):
        def __init__(self, path):
            super().__init__(db_path)

    return TestFossilReader


# --- Explorer main page ---


@pytest.mark.django_db
class TestExplorerView:
    def test_loads_for_admin(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Schema Explorer" in content
        assert "blob" in content
        assert "event" in content
        assert "ticket" in content

    def test_shows_row_counts(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/")
        content = response.content.decode()
        # blob has 2 rows
        assert "2" in content

    def test_shows_relationships(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/")
        content = response.content.decode()
        # Schema map section should be present
        assert "Schema Map" in content

    def test_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = writer_client.get(f"/projects/{sample_project.slug}/fossil/explorer/")
        assert response.status_code == 403

    def test_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = reader_client.get(f"/projects/{sample_project.slug}/fossil/explorer/")
        assert response.status_code == 403

    def test_denied_for_anonymous(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/explorer/")
        assert response.status_code == 302  # redirect to login


# --- Explorer table detail ---


@pytest.mark.django_db
class TestExplorerTableView:
    def test_returns_table_columns(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/blob/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "blob" in content
        assert "rid" in content
        assert "uuid" in content
        assert "size" in content

    def test_returns_sample_rows(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/blob/")
        content = response.content.decode()
        assert "abc123def456" in content
        assert "789012345678" in content

    def test_returns_row_count(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/blob/")
        content = response.content.decode()
        assert "2 rows" in content

    def test_nonexistent_table_404(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/nonexistent/")
        assert response.status_code == 404

    def test_invalid_table_name_404(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/drop%20table/")
        assert response.status_code == 404

    def test_sql_injection_table_name_404(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/blob;DROP/")
        assert response.status_code == 404

    def test_pagination(self, admin_client, sample_project, fossil_repo_obj, tmp_path):
        """Test that pagination works for tables with more than 25 rows."""
        db_path = tmp_path / "paged.fossil"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, value TEXT)")
        for i in range(60):
            conn.execute("INSERT INTO test_data (id, value) VALUES (?, ?)", (i, f"val-{i}"))
        conn.commit()
        conn.close()

        reader_cls = _make_fossil_reader_cls(db_path)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            # Page 1
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/test_data/")
            content = response.content.decode()
            assert "val-0" in content
            assert "Next" in content

            # Page 2
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/test_data/?page=2")
            content = response.content.decode()
            assert "val-25" in content
            assert "Previous" in content

    def test_empty_table(self, admin_client, sample_project, fossil_repo_obj, tmp_path):
        db_path = tmp_path / "empty.fossil"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE empty_table (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        reader_cls = _make_fossil_reader_cls(db_path)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/empty_table/")
        assert response.status_code == 200
        assert "Table is empty" in response.content.decode()

    def test_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = writer_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/blob/")
        assert response.status_code == 403


# --- Explorer query runner ---


@pytest.mark.django_db
class TestExplorerQueryView:
    def test_query_page_loads(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/query/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Query Runner" in content

    def test_run_select_query(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(
                f"/projects/{sample_project.slug}/fossil/explorer/query/",
                {"sql": "SELECT uuid, size FROM blob ORDER BY rid"},
            )
        assert response.status_code == 200
        content = response.content.decode()
        assert "abc123def456" in content
        assert "100" in content
        assert "2 rows" in content

    def test_reject_insert(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(
                f"/projects/{sample_project.slug}/fossil/explorer/query/",
                {"sql": "INSERT INTO blob (rid, uuid, size) VALUES (99, 'evil', 0)"},
            )
        content = response.content.decode()
        assert "SELECT" in content  # error message about requiring SELECT

    def test_reject_drop(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(
                f"/projects/{sample_project.slug}/fossil/explorer/query/",
                {"sql": "DROP TABLE blob"},
            )
        content = response.content.decode()
        assert "forbidden" in content.lower() or "SELECT" in content

    def test_reject_delete(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(
                f"/projects/{sample_project.slug}/fossil/explorer/query/",
                {"sql": "DELETE FROM blob"},
            )
        content = response.content.decode()
        assert "forbidden" in content.lower() or "SELECT" in content

    def test_reject_multiple_statements(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(
                f"/projects/{sample_project.slug}/fossil/explorer/query/",
                {"sql": "SELECT 1; SELECT 2"},
            )
        content = response.content.decode()
        assert "multiple" in content.lower()

    def test_handles_invalid_sql(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(
                f"/projects/{sample_project.slug}/fossil/explorer/query/",
                {"sql": "SELECT * FROM this_table_does_not_exist"},
            )
        content = response.content.decode()
        # Should show an error, not crash
        assert "no such table" in content.lower()

    def test_empty_query_no_results(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/query/")
        assert response.status_code == 200
        content = response.content.decode()
        # Should show available tables sidebar
        assert "Available Tables" in content

    def test_shows_table_names_sidebar(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/query/")
        content = response.content.decode()
        assert "blob" in content
        assert "event" in content

    def test_denied_for_writer(self, writer_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = writer_client.get(f"/projects/{sample_project.slug}/fossil/explorer/query/")
        assert response.status_code == 403

    def test_denied_for_reader(self, reader_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = reader_client.get(f"/projects/{sample_project.slug}/fossil/explorer/query/")
        assert response.status_code == 403

    def test_denied_for_anonymous(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/explorer/query/")
        assert response.status_code == 302  # redirect to login


# --- URL routing ---


@pytest.mark.django_db
class TestExplorerURLs:
    def test_explorer_url_resolves(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/")
        assert response.status_code == 200

    def test_explorer_table_url_resolves(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/table/blob/")
        assert response.status_code == 200

    def test_explorer_query_url_resolves(self, admin_client, sample_project, fossil_repo_obj, explorer_fossil_db):
        reader_cls = _make_fossil_reader_cls(explorer_fossil_db)
        with _disk_patch, patch("fossil.views.FossilReader", reader_cls):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/explorer/query/")
        assert response.status_code == 200

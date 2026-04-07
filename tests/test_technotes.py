import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fossil.models import FossilRepository
from fossil.reader import FossilReader

# Reusable patch that makes FossilRepository.exists_on_disk return True
_disk_patch = patch("fossil.models.FossilRepository.exists_on_disk", new_callable=lambda: property(lambda self: True))


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


def _create_test_fossil_db(path: Path):
    """Create a minimal .fossil SQLite database with the tables reader.py needs."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE blob (
            rid INTEGER PRIMARY KEY,
            uuid TEXT UNIQUE NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            content BLOB
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE event (
            type TEXT,
            mtime REAL,
            objid INTEGER,
            user TEXT,
            comment TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert_technote(conn, rid, uuid, mtime, user, comment, body_content=""):
    """Insert a technote event and blob into the test database.

    Technotes use event.type = 'e'. The blob contains a Fossil wiki artifact
    format: header cards followed by W <size>\\n<content>\\nZ <hash>.
    """
    import struct
    import zlib

    # Build a minimal Fossil wiki artifact containing the body
    artifact = f"D 2024-01-01T00:00:00\nU {user}\nW {len(body_content.encode('utf-8'))}\n{body_content}\nZ 0000000000000000"
    raw_bytes = artifact.encode("utf-8")

    # Fossil stores blobs with a 4-byte big-endian size prefix + zlib compressed content
    compressed = struct.pack(">I", len(raw_bytes)) + zlib.compress(raw_bytes)

    conn.execute("INSERT INTO blob (rid, uuid, size, content) VALUES (?, ?, ?, ?)", (rid, uuid, len(raw_bytes), compressed))
    conn.execute("INSERT INTO event (type, mtime, objid, user, comment) VALUES ('e', ?, ?, ?, ?)", (mtime, rid, user, comment))
    conn.commit()


@pytest.fixture
def fossil_db(tmp_path):
    """Create a temporary .fossil file with technote data for reader tests."""
    db_path = tmp_path / "test.fossil"
    conn = _create_test_fossil_db(db_path)
    _insert_technote(conn, 100, "abc123def456", 2460676.5, "admin", "First technote", "# Hello\n\nThis is the body.")
    _insert_technote(conn, 101, "xyz789ghi012", 2460677.5, "dev", "Second technote", "Another note body.")
    conn.close()
    return db_path


def _make_reader_mock(**methods):
    """Create a MagicMock that replaces FossilReader as a class.

    The returned mock supports:
        reader = FossilReader(path)   # returns a mock instance
        with reader:                  # context manager
            reader.some_method()      # returns configured value
    """
    mock_cls = MagicMock()
    # The instance returned by calling the class
    instance = MagicMock()
    mock_cls.return_value = instance
    # Context manager support: __enter__ returns the same instance
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    for name, val in methods.items():
        getattr(instance, name).return_value = val
    return mock_cls


# --- Reader unit tests (no Django DB needed) ---


class TestGetTechnotes:
    def test_returns_technotes(self, fossil_db):
        reader = FossilReader(fossil_db)
        with reader:
            notes = reader.get_technotes()
        assert len(notes) == 2
        assert notes[0]["uuid"] == "xyz789ghi012"  # Most recent first
        assert notes[1]["uuid"] == "abc123def456"

    def test_technote_fields(self, fossil_db):
        reader = FossilReader(fossil_db)
        with reader:
            notes = reader.get_technotes()
        note = notes[1]  # The first inserted one
        assert note["user"] == "admin"
        assert note["comment"] == "First technote"
        assert note["timestamp"] is not None

    def test_empty_repo(self, tmp_path):
        db_path = tmp_path / "empty.fossil"
        conn = _create_test_fossil_db(db_path)
        conn.close()
        reader = FossilReader(db_path)
        with reader:
            notes = reader.get_technotes()
        assert notes == []


class TestGetTechnoteDetail:
    def test_returns_detail_with_body(self, fossil_db):
        reader = FossilReader(fossil_db)
        with reader:
            note = reader.get_technote_detail("abc123def456")
        assert note is not None
        assert note["uuid"] == "abc123def456"
        assert note["comment"] == "First technote"
        assert "# Hello" in note["body"]
        assert "This is the body." in note["body"]

    def test_prefix_match(self, fossil_db):
        reader = FossilReader(fossil_db)
        with reader:
            note = reader.get_technote_detail("abc123")
        assert note is not None
        assert note["uuid"] == "abc123def456"

    def test_not_found(self, fossil_db):
        reader = FossilReader(fossil_db)
        with reader:
            note = reader.get_technote_detail("nonexistent")
        assert note is None


class TestGetUnversionedFiles:
    def test_returns_files(self, tmp_path):
        db_path = tmp_path / "uv.fossil"
        conn = _create_test_fossil_db(db_path)
        conn.execute(
            """
            CREATE TABLE unversioned (
                uvid INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                rcvid INTEGER,
                mtime DATETIME,
                hash TEXT,
                sz INTEGER,
                encoding INT,
                content BLOB
            )
            """
        )
        conn.execute(
            "INSERT INTO unversioned (name, mtime, hash, sz, encoding, content) VALUES (?, ?, ?, ?, ?, ?)",
            ("readme.txt", 1700000000, "abc123hash", 42, 0, b"file content"),
        )
        conn.execute(
            "INSERT INTO unversioned (name, mtime, hash, sz, encoding, content) VALUES (?, ?, ?, ?, ?, ?)",
            ("bin/app.tar.gz", 1700001000, "def456hash", 1024, 0, b"tarball"),
        )
        conn.commit()
        conn.close()

        reader = FossilReader(db_path)
        with reader:
            files = reader.get_unversioned_files()
        assert len(files) == 2
        assert files[0]["name"] == "bin/app.tar.gz"  # Alphabetical
        assert files[1]["name"] == "readme.txt"
        assert files[1]["size"] == 42
        assert files[1]["hash"] == "abc123hash"
        assert files[1]["mtime"] is not None

    def test_no_unversioned_table(self, tmp_path):
        """Repos without unversioned content don't have the table -- should return empty."""
        db_path = tmp_path / "no_uv.fossil"
        conn = _create_test_fossil_db(db_path)
        conn.close()
        reader = FossilReader(db_path)
        with reader:
            files = reader.get_unversioned_files()
        assert files == []

    def test_deleted_files_excluded(self, tmp_path):
        """Deleted UV files have empty hash -- should be excluded."""
        db_path = tmp_path / "del_uv.fossil"
        conn = _create_test_fossil_db(db_path)
        conn.execute(
            """
            CREATE TABLE unversioned (
                uvid INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE, rcvid INTEGER, mtime DATETIME,
                hash TEXT, sz INTEGER, encoding INT, content BLOB
            )
            """
        )
        conn.execute(
            "INSERT INTO unversioned (name, mtime, hash, sz) VALUES (?, ?, ?, ?)",
            ("alive.txt", 1700000000, "somehash", 10),
        )
        conn.execute(
            "INSERT INTO unversioned (name, mtime, hash, sz) VALUES (?, ?, ?, ?)",
            ("deleted.txt", 1700000000, "", 0),
        )
        conn.commit()
        conn.close()

        reader = FossilReader(db_path)
        with reader:
            files = reader.get_unversioned_files()
        assert len(files) == 1
        assert files[0]["name"] == "alive.txt"


# --- View tests (Django DB needed) ---


@pytest.mark.django_db
class TestTechnoteListView:
    def test_list_page_loads(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock(get_technotes=[{"uuid": "abc123", "timestamp": "2024-01-01", "user": "admin", "comment": "Test note"}])
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/technotes/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Technotes" in content
        assert "Test note" in content

    def test_list_shows_create_button_for_writer(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock(get_technotes=[])
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/technotes/")
        assert response.status_code == 200
        assert "New Technote" in response.content.decode()

    def test_list_hides_create_button_for_reader(self, sample_project, fossil_repo_obj):
        from django.contrib.auth.models import User
        from django.test import Client

        User.objects.create_user(username="reader_only", password="testpass123")
        c = Client()
        c.login(username="reader_only", password="testpass123")
        sample_project.visibility = "public"
        sample_project.save()
        mock = _make_reader_mock(get_technotes=[])
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = c.get(f"/projects/{sample_project.slug}/fossil/technotes/")
        assert response.status_code == 200
        assert "New Technote" not in response.content.decode()

    def test_list_denied_for_no_perm_on_private(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/technotes/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestTechnoteCreateView:
    def test_get_form(self, admin_client, sample_project, fossil_repo_obj):
        with _disk_patch:
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/technotes/create/")
        assert response.status_code == 200
        assert "New Technote" in response.content.decode()

    def test_create_technote(self, admin_client, sample_project, fossil_repo_obj):
        mock_cli = MagicMock()
        mock_cli.return_value.technote_create.return_value = True
        with _disk_patch, patch("fossil.cli.FossilCLI", mock_cli):
            response = admin_client.post(
                f"/projects/{sample_project.slug}/fossil/technotes/create/",
                {"title": "My Note", "body": "Note body content", "timestamp": ""},
            )
        assert response.status_code == 302  # Redirect to list

    def test_create_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/technotes/create/")
        assert response.status_code == 302  # Redirect to login

    def test_create_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.post(
            f"/projects/{sample_project.slug}/fossil/technotes/create/",
            {"title": "Nope", "body": "denied"},
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestTechnoteDetailView:
    def test_detail_page(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock(
            get_technote_detail={
                "uuid": "abc123def456",
                "timestamp": "2024-01-01",
                "user": "admin",
                "comment": "Test technote",
                "body": "# Hello\n\nBody text.",
            }
        )
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/technotes/abc123def456/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Test technote" in content
        assert "Body text" in content

    def test_detail_not_found(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock(get_technote_detail=None)
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/technotes/nonexistent/")
        assert response.status_code == 404

    def test_detail_denied_for_no_perm_on_private(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/technotes/abc123/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestTechnoteEditView:
    def test_get_edit_form(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock(
            get_technote_detail={
                "uuid": "abc123def456",
                "timestamp": "2024-01-01",
                "user": "admin",
                "comment": "Test technote",
                "body": "Existing body content",
            }
        )
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/technotes/abc123def456/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Existing body content" in content

    def test_edit_technote(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock(
            get_technote_detail={
                "uuid": "abc123def456",
                "timestamp": "2024-01-01",
                "user": "admin",
                "comment": "Test technote",
                "body": "Old body",
            }
        )
        mock_cli = MagicMock()
        mock_cli.return_value.technote_edit.return_value = True
        with _disk_patch, patch("fossil.views.FossilReader", mock), patch("fossil.cli.FossilCLI", mock_cli):
            response = admin_client.post(
                f"/projects/{sample_project.slug}/fossil/technotes/abc123def456/edit/",
                {"body": "Updated body content"},
            )
        assert response.status_code == 302  # Redirect to detail

    def test_edit_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/technotes/abc123/edit/")
        assert response.status_code == 302  # Redirect to login

    def test_edit_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.post(
            f"/projects/{sample_project.slug}/fossil/technotes/abc123/edit/",
            {"body": "denied"},
        )
        assert response.status_code == 403

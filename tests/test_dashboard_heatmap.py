"""Tests for the dashboard activity heatmap."""

import json
import sqlite3
from pathlib import Path

import pytest
from django.test import Client

from fossil.models import FossilRepository
from fossil.reader import FossilReader


def _create_test_fossil_db(path: Path, checkin_days_ago: list[int] | None = None):
    """Create a minimal .fossil SQLite database with event data for testing.

    Args:
        path: Where to write the .fossil file.
        checkin_days_ago: List of integers representing days ago for each checkin.
            Multiple entries for the same day create multiple checkins on that day.

    Note: Uses SQLite's julianday('now') for the reference point so that the
    date(mtime - 0.5) conversion in reader.py queries produces consistent dates.
    Python datetime vs SQLite julianday can differ by fractions of a second,
    which at day boundaries shifts the resulting date.
    """
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS config (name TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO config VALUES ('project-name', 'test-project')")
    conn.execute("INSERT OR REPLACE INTO config VALUES ('project-code', 'abc123')")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS event (
            type TEXT, mtime REAL, objid INTEGER, tagid INTEGER,
            uid INTEGER, bgcolor TEXT, euser TEXT, user TEXT,
            ecomment TEXT, comment TEXT, brief TEXT,
            omtime REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS blob (
            rid INTEGER PRIMARY KEY, rcvid INTEGER, size INTEGER,
            uuid TEXT UNIQUE, content BLOB
        )"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS tag (tagid INTEGER PRIMARY KEY, tagname TEXT, tagtype INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS ticket (tkt_id TEXT PRIMARY KEY, tkt_uuid TEXT)")

    if checkin_days_ago:
        # Use SQLite's own julianday('now') so the reference point matches
        # what the reader.py queries will use for date calculations.
        now_julian = conn.execute("SELECT julianday('now')").fetchone()[0]
        for i, days in enumerate(checkin_days_ago):
            mtime = now_julian - days
            conn.execute("INSERT INTO blob VALUES (?, 0, 100, ?, NULL)", (i + 1, f"uuid{i:04d}"))
            conn.execute(
                "INSERT INTO event (type, mtime, objid, user, comment) VALUES ('ci', ?, ?, 'testuser', 'commit')",
                (mtime, i + 1),
            )

    conn.commit()
    conn.close()


class TestFossilReaderDailyActivity:
    """Tests for FossilReader.get_daily_commit_activity()."""

    def test_returns_empty_for_no_checkins(self, tmp_path):
        db_path = tmp_path / "empty.fossil"
        _create_test_fossil_db(db_path, checkin_days_ago=[])
        with FossilReader(db_path) as reader:
            result = reader.get_daily_commit_activity(days=365)
        assert result == []

    def test_returns_daily_counts(self, tmp_path):
        # 3 checkins at 5 days ago, 1 checkin at 10 days ago
        db_path = tmp_path / "active.fossil"
        _create_test_fossil_db(db_path, checkin_days_ago=[5, 5, 5, 10])
        with FossilReader(db_path) as reader:
            result = reader.get_daily_commit_activity(days=365)

        counts_by_date = {entry["date"]: entry["count"] for entry in result}

        # Should have 2 distinct dates with counts 3 and 1
        assert len(counts_by_date) == 2
        counts = sorted(counts_by_date.values())
        assert counts == [1, 3]

    def test_excludes_old_data_outside_window(self, tmp_path):
        # One checkin 10 days ago, one 400 days ago
        db_path = tmp_path / "old.fossil"
        _create_test_fossil_db(db_path, checkin_days_ago=[10, 400])
        with FossilReader(db_path) as reader:
            result = reader.get_daily_commit_activity(days=365)

        dates = [entry["date"] for entry in result]
        assert len(dates) == 1  # only the 10-day-ago entry

    def test_custom_day_window(self, tmp_path):
        # Checkins at 5, 20, and 40 days ago -- with a 30-day window
        db_path = tmp_path / "window.fossil"
        _create_test_fossil_db(db_path, checkin_days_ago=[5, 20, 40])
        with FossilReader(db_path) as reader:
            result = reader.get_daily_commit_activity(days=30)

        dates = [entry["date"] for entry in result]
        assert len(dates) == 2  # 5 and 20 days ago; 40 is outside window

    def test_results_sorted_by_date(self, tmp_path):
        db_path = tmp_path / "sorted.fossil"
        _create_test_fossil_db(db_path, checkin_days_ago=[30, 10, 20, 5])
        with FossilReader(db_path) as reader:
            result = reader.get_daily_commit_activity(days=365)

        dates = [entry["date"] for entry in result]
        assert dates == sorted(dates)

    def test_handles_missing_event_table(self, tmp_path):
        # A .fossil file that has no event table at all
        db_path = tmp_path / "broken.fossil"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE config (name TEXT, value TEXT)")
        conn.commit()
        conn.close()

        with FossilReader(db_path) as reader:
            result = reader.get_daily_commit_activity(days=365)
        assert result == []


@pytest.mark.django_db
class TestDashboardHeatmapView:
    """Tests for the heatmap data in the dashboard view."""

    def test_unauthenticated_redirects_to_login(self):
        client = Client()
        response = client.get("/dashboard/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_dashboard_returns_heatmap_json(self, admin_client):
        response = admin_client.get("/dashboard/")
        assert response.status_code == 200
        assert "heatmap_json" in response.context

        # With no repos on disk, heatmap should be an empty JSON array
        heatmap = json.loads(response.context["heatmap_json"])
        assert isinstance(heatmap, list)

    def test_dashboard_heatmap_aggregates_across_repos(self, admin_client, admin_user, sample_project, tmp_path):
        """Create two repos with overlapping daily activity and verify aggregation.

        Uses days well in the past (5 and 10) to avoid date-boundary issues
        caused by Fossil's Julian-day-to-date conversion (date(mtime - 0.5)).
        """
        from constance import config

        from organization.models import Organization
        from projects.models import Project

        # Use the auto-created repo from the signal (Project post_save creates a FossilRepository)
        repo1 = FossilRepository.objects.get(project=sample_project)
        repo1.filename = "repo1.fossil"
        repo1.save(update_fields=["filename", "updated_at", "version"])

        # Need a second project for the second repo (OneToOne constraint)
        org = Organization.objects.first()
        project2 = Project.objects.create(name="Second Project", organization=org, visibility="private", created_by=admin_user)
        repo2 = FossilRepository.objects.get(project=project2)
        repo2.filename = "repo2.fossil"
        repo2.save(update_fields=["filename", "updated_at", "version"])

        # Create .fossil files at the paths full_path resolves to (FOSSIL_DATA_DIR/filename)
        original_dir = config.FOSSIL_DATA_DIR
        config.FOSSIL_DATA_DIR = str(tmp_path)
        try:
            _create_test_fossil_db(tmp_path / "repo1.fossil", checkin_days_ago=[5, 5, 10])  # 2 at day-5, 1 at day-10
            _create_test_fossil_db(tmp_path / "repo2.fossil", checkin_days_ago=[5, 10, 10])  # 1 at day-5, 2 at day-10

            response = admin_client.get("/dashboard/")
        finally:
            config.FOSSIL_DATA_DIR = original_dir

        assert response.status_code == 200
        heatmap = json.loads(response.context["heatmap_json"])
        counts_by_date = {entry["date"]: entry["count"] for entry in heatmap}

        # Aggregated: 3 at day-5, 3 at day-10 = 6 total across 2 dates
        assert len(counts_by_date) == 2
        assert sum(counts_by_date.values()) == 6
        # Each date should have exactly 3 commits (2+1 and 1+2)
        for count in counts_by_date.values():
            assert count == 3

    def test_dashboard_heatmap_json_is_sorted(self, admin_client, admin_user, sample_project, tmp_path):
        from constance import config

        # Use the auto-created repo from the signal
        repo = FossilRepository.objects.get(project=sample_project)

        original_dir = config.FOSSIL_DATA_DIR
        config.FOSSIL_DATA_DIR = str(tmp_path)
        try:
            _create_test_fossil_db(tmp_path / repo.filename, checkin_days_ago=[30, 5, 20, 10])
            response = admin_client.get("/dashboard/")
        finally:
            config.FOSSIL_DATA_DIR = original_dir

        heatmap = json.loads(response.context["heatmap_json"])
        dates = [entry["date"] for entry in heatmap]
        assert dates == sorted(dates)

    def test_dashboard_heatmap_skips_missing_repos(self, admin_client, admin_user, sample_project):
        """Repos where the file doesn't exist on disk should be silently skipped."""
        # The signal already created a FossilRepository -- just update the filename
        repo = FossilRepository.objects.get(project=sample_project)
        repo.filename = "nonexistent.fossil"
        repo.save(update_fields=["filename", "updated_at", "version"])

        response = admin_client.get("/dashboard/")
        assert response.status_code == 200
        heatmap = json.loads(response.context["heatmap_json"])
        assert heatmap == []

    def test_dashboard_renders_heatmap_container(self, admin_client, admin_user, sample_project, tmp_path):
        """When heatmap data exists, the template should include the heatmap div."""
        from constance import config

        # Use the auto-created repo from the signal
        repo = FossilRepository.objects.get(project=sample_project)

        original_dir = config.FOSSIL_DATA_DIR
        config.FOSSIL_DATA_DIR = str(tmp_path)
        try:
            _create_test_fossil_db(tmp_path / repo.filename, checkin_days_ago=[5, 10, 15])
            response = admin_client.get("/dashboard/")
        finally:
            config.FOSSIL_DATA_DIR = original_dir

        content = response.content.decode()
        assert 'id="heatmap"' in content
        assert "Activity (last year)" in content
        assert "Less" in content
        assert "More" in content

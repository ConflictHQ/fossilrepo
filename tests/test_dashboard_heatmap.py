"""Tests for the dashboard activity heatmap."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import PropertyMock, patch

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
        now_julian = _datetime_to_julian(datetime.now(UTC))
        for i, days in enumerate(checkin_days_ago):
            mtime = now_julian - days
            conn.execute("INSERT INTO blob VALUES (?, 0, 100, ?, NULL)", (i + 1, f"uuid{i:04d}"))
            conn.execute(
                "INSERT INTO event (type, mtime, objid, user, comment) VALUES ('ci', ?, ?, 'testuser', 'commit')",
                (mtime, i + 1),
            )

    conn.commit()
    conn.close()


def _datetime_to_julian(dt: datetime) -> float:
    """Convert a Python datetime to Julian day number."""
    unix_ts = dt.timestamp()
    return unix_ts / 86400.0 + 2440587.5


class TestFossilReaderDailyActivity:
    """Tests for FossilReader.get_daily_commit_activity()."""

    def test_returns_empty_for_no_checkins(self, tmp_path):
        db_path = tmp_path / "empty.fossil"
        _create_test_fossil_db(db_path, checkin_days_ago=[])
        with FossilReader(db_path) as reader:
            result = reader.get_daily_commit_activity(days=365)
        assert result == []

    def test_returns_daily_counts(self, tmp_path):
        # 3 checkins today, 1 checkin yesterday
        db_path = tmp_path / "active.fossil"
        _create_test_fossil_db(db_path, checkin_days_ago=[0, 0, 0, 1])
        with FossilReader(db_path) as reader:
            result = reader.get_daily_commit_activity(days=365)

        counts_by_date = {entry["date"]: entry["count"] for entry in result}
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")

        assert counts_by_date.get(today) == 3
        assert counts_by_date.get(yesterday) == 1

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
        """Create two repos with overlapping daily activity and verify aggregation."""
        # Create two .fossil files with overlapping dates
        db1 = tmp_path / "repo1.fossil"
        db2 = tmp_path / "repo2.fossil"
        _create_test_fossil_db(db1, checkin_days_ago=[0, 0, 1])  # 2 today, 1 yesterday
        _create_test_fossil_db(db2, checkin_days_ago=[0, 1, 1])  # 1 today, 2 yesterday

        repo1 = FossilRepository.objects.create(project=sample_project, filename="repo1.fossil", created_by=admin_user)

        # Need a second project for the second repo (OneToOne constraint)
        from organization.models import Organization
        from projects.models import Project

        org = Organization.objects.first()
        project2 = Project.objects.create(name="Second Project", organization=org, visibility="private", created_by=admin_user)
        repo2 = FossilRepository.objects.create(project=project2, filename="repo2.fossil", created_by=admin_user)

        # Patch full_path for both repos to point to our test files
        with (
            patch.object(type(repo1), "full_path", new_callable=PropertyMock, return_value=db1),
            patch.object(type(repo2), "full_path", new_callable=PropertyMock, return_value=db2),
            patch.object(type(repo1), "exists_on_disk", new_callable=PropertyMock, return_value=True),
            patch.object(type(repo2), "exists_on_disk", new_callable=PropertyMock, return_value=True),
        ):
            response = admin_client.get("/dashboard/")

        assert response.status_code == 200
        heatmap = json.loads(response.context["heatmap_json"])
        counts_by_date = {entry["date"]: entry["count"] for entry in heatmap}

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")

        # 2 + 1 = 3 today, 1 + 2 = 3 yesterday
        assert counts_by_date.get(today) == 3
        assert counts_by_date.get(yesterday) == 3

    def test_dashboard_heatmap_json_is_sorted(self, admin_client, admin_user, sample_project, tmp_path):
        db = tmp_path / "sorted.fossil"
        _create_test_fossil_db(db, checkin_days_ago=[30, 5, 20, 10])

        repo = FossilRepository.objects.create(project=sample_project, filename="sorted.fossil", created_by=admin_user)

        with (
            patch.object(type(repo), "full_path", new_callable=PropertyMock, return_value=db),
            patch.object(type(repo), "exists_on_disk", new_callable=PropertyMock, return_value=True),
        ):
            response = admin_client.get("/dashboard/")

        heatmap = json.loads(response.context["heatmap_json"])
        dates = [entry["date"] for entry in heatmap]
        assert dates == sorted(dates)

    def test_dashboard_heatmap_skips_missing_repos(self, admin_client, admin_user, sample_project):
        """Repos where the file doesn't exist on disk should be silently skipped."""
        FossilRepository.objects.create(project=sample_project, filename="nonexistent.fossil", created_by=admin_user)

        response = admin_client.get("/dashboard/")
        assert response.status_code == 200
        heatmap = json.loads(response.context["heatmap_json"])
        assert heatmap == []

    def test_dashboard_renders_heatmap_container(self, admin_client, admin_user, sample_project, tmp_path):
        """When heatmap data exists, the template should include the heatmap div."""
        db = tmp_path / "vis.fossil"
        _create_test_fossil_db(db, checkin_days_ago=[0, 1, 2])

        repo = FossilRepository.objects.create(project=sample_project, filename="vis.fossil", created_by=admin_user)

        with (
            patch.object(type(repo), "full_path", new_callable=PropertyMock, return_value=db),
            patch.object(type(repo), "exists_on_disk", new_callable=PropertyMock, return_value=True),
        ):
            response = admin_client.get("/dashboard/")

        content = response.content.decode()
        assert 'id="heatmap"' in content
        assert "Activity (last year)" in content
        assert "Less" in content
        assert "More" in content

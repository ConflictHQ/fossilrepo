"""Tests for fossil/tasks.py and accounts/views.py uncovered lines.

Targets:
  - fossil/tasks.py (33% -> higher): sync_metadata, create_snapshot,
    check_upstream, run_git_sync, dispatch_notifications,
    sync_tickets_to_github, sync_wiki_to_github
  - accounts/views.py (77% -> higher): _sanitize_ssh_key, _verify_turnstile,
    login turnstile flow, ssh key CRUD, notification prefs HTMX,
    profile_token_create edge cases
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from fossil.models import FossilRepository, FossilSnapshot
from fossil.notifications import Notification, NotificationPreference, ProjectWatch
from fossil.reader import TicketEntry, TimelineEntry, WikiPage
from fossil.sync_models import GitMirror, SyncLog, TicketSyncMapping, WikiSyncMapping
from fossil.webhooks import Webhook, WebhookDelivery

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Reusable patch that makes FossilRepository.exists_on_disk return True
_disk_exists = patch(
    "fossil.models.FossilRepository.exists_on_disk",
    new_callable=lambda: property(lambda self: True),
)


def _make_reader_mock(**methods):
    """Create a context-manager-compatible FossilReader mock."""
    mock_cls = MagicMock()
    instance = MagicMock()
    mock_cls.return_value = instance
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    for name, val in methods.items():
        getattr(instance, name).return_value = val
    return mock_cls


def _make_timeline_entry(**overrides):
    defaults = {
        "rid": 1,
        "uuid": "abc123def456",
        "event_type": "ci",
        "timestamp": datetime.now(UTC),
        "user": "dev",
        "comment": "fix typo",
        "branch": "trunk",
    }
    defaults.update(overrides)
    return TimelineEntry(**defaults)


def _make_ticket(**overrides):
    defaults = {
        "uuid": "ticket-uuid-001",
        "title": "Bug report",
        "status": "open",
        "type": "bug",
        "created": datetime.now(UTC),
        "owner": "dev",
        "body": "Something is broken",
        "priority": "high",
        "severity": "critical",
    }
    defaults.update(overrides)
    return TicketEntry(**defaults)


def _make_wiki_page(**overrides):
    defaults = {
        "name": "Home",
        "content": "# Welcome",
        "last_modified": datetime.now(UTC),
        "user": "dev",
    }
    defaults.update(overrides)
    return WikiPage(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def mirror(fossil_repo_obj, admin_user):
    return GitMirror.objects.create(
        repository=fossil_repo_obj,
        git_remote_url="https://github.com/testorg/testrepo.git",
        auth_method="token",
        auth_credential="ghp_testtoken123",
        sync_direction="push",
        sync_mode="scheduled",
        sync_tickets=False,
        sync_wiki=False,
        created_by=admin_user,
    )


@pytest.fixture
def webhook(fossil_repo_obj, admin_user):
    return Webhook.objects.create(
        repository=fossil_repo_obj,
        url="https://hooks.example.com/test",
        secret="test-secret",
        events="all",
        is_active=True,
        created_by=admin_user,
    )


# ===================================================================
# fossil/tasks.py -- sync_repository_metadata
# ===================================================================


@pytest.mark.django_db
class TestSyncRepositoryMetadata:
    """Test the sync_metadata periodic task."""

    def test_updates_metadata_from_reader(self, fossil_repo_obj):
        """Task reads the .fossil file and updates checkin_count, file_size, project_code."""
        from fossil.tasks import sync_repository_metadata

        timeline_entry = _make_timeline_entry()
        reader_mock = _make_reader_mock(
            get_checkin_count=42,
            get_timeline=[timeline_entry],
            get_project_code="abc123project",
        )

        fake_stat = MagicMock()
        fake_stat.st_size = 98765

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch.object(type(fossil_repo_obj), "full_path", new_callable=PropertyMock) as mock_path,
        ):
            mock_path.return_value = MagicMock()
            mock_path.return_value.stat.return_value = fake_stat

            sync_repository_metadata()

        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.checkin_count == 42
        assert fossil_repo_obj.file_size_bytes == 98765
        assert fossil_repo_obj.fossil_project_code == "abc123project"
        assert fossil_repo_obj.last_checkin_at == timeline_entry.timestamp

    def test_skips_repo_not_on_disk(self, fossil_repo_obj):
        """Repos that don't exist on disk should be skipped without error."""
        from fossil.tasks import sync_repository_metadata

        with patch(
            "fossil.models.FossilRepository.exists_on_disk",
            new_callable=lambda: property(lambda self: False),
        ):
            # Should complete without error
            sync_repository_metadata()

        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.checkin_count == 0  # unchanged

    def test_handles_empty_timeline(self, fossil_repo_obj):
        """When timeline is empty, last_checkin_at stays None."""
        from fossil.tasks import sync_repository_metadata

        reader_mock = _make_reader_mock(
            get_checkin_count=0,
            get_timeline=[],
            get_project_code="proj-code",
        )

        fake_stat = MagicMock()
        fake_stat.st_size = 1024

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch.object(type(fossil_repo_obj), "full_path", new_callable=PropertyMock) as mock_path,
        ):
            mock_path.return_value = MagicMock()
            mock_path.return_value.stat.return_value = fake_stat

            sync_repository_metadata()

        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.last_checkin_at is None

    def test_handles_reader_exception(self, fossil_repo_obj):
        """If FossilReader raises, the task logs and moves on."""
        from fossil.tasks import sync_repository_metadata

        reader_mock = MagicMock(side_effect=Exception("corrupt db"))

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch.object(type(fossil_repo_obj), "full_path", new_callable=PropertyMock) as mock_path,
        ):
            mock_path.return_value = MagicMock()
            mock_path.return_value.stat.side_effect = Exception("stat failed")

            # Should not raise
            sync_repository_metadata()


# ===================================================================
# fossil/tasks.py -- create_snapshot
# ===================================================================


@pytest.mark.django_db
class TestCreateSnapshot:
    """Test the create_snapshot task."""

    def _mock_config(self, store_in_db=True):
        """Build a constance config mock with FOSSIL_STORE_IN_DB set."""
        cfg = MagicMock()
        cfg.FOSSIL_STORE_IN_DB = store_in_db
        return cfg

    def test_creates_snapshot_when_enabled(self, fossil_repo_obj, tmp_path, settings):
        """Snapshot is created when FOSSIL_STORE_IN_DB is True."""
        from fossil.tasks import create_snapshot

        # Ensure default file storage is configured for the test
        settings.STORAGES = {
            **settings.STORAGES,
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        }
        settings.MEDIA_ROOT = str(tmp_path / "media")

        # Write a fake fossil file
        fossil_file = tmp_path / "test.fossil"
        fossil_file.write_bytes(b"FAKE FOSSIL DATA 12345")

        with (
            patch("constance.config", self._mock_config(store_in_db=True)),
            patch.object(type(fossil_repo_obj), "full_path", new_callable=PropertyMock, return_value=fossil_file),
            _disk_exists,
        ):
            create_snapshot(fossil_repo_obj.pk, note="manual backup")

        snapshot = FossilSnapshot.objects.filter(repository=fossil_repo_obj).first()
        assert snapshot is not None
        assert snapshot.note == "manual backup"
        assert snapshot.file_size_bytes == len(b"FAKE FOSSIL DATA 12345")
        assert snapshot.fossil_hash  # should be a sha256 hex string
        assert len(snapshot.fossil_hash) == 64

    def test_skips_when_store_in_db_disabled(self, fossil_repo_obj):
        """No snapshot created when FOSSIL_STORE_IN_DB is False."""
        from fossil.tasks import create_snapshot

        with patch("constance.config", self._mock_config(store_in_db=False)):
            create_snapshot(fossil_repo_obj.pk, note="should not exist")

        assert FossilSnapshot.objects.filter(repository=fossil_repo_obj).count() == 0

    def test_skips_for_nonexistent_repo(self):
        """Returns early for a repository ID that doesn't exist."""
        from fossil.tasks import create_snapshot

        with patch("constance.config", self._mock_config(store_in_db=True)):
            # Should not raise
            create_snapshot(99999, note="orphan")

        assert FossilSnapshot.objects.count() == 0

    def test_skips_when_not_on_disk(self, fossil_repo_obj):
        """Returns early when the file doesn't exist on disk."""
        from fossil.tasks import create_snapshot

        with (
            patch("constance.config", self._mock_config(store_in_db=True)),
            patch(
                "fossil.models.FossilRepository.exists_on_disk",
                new_callable=lambda: property(lambda self: False),
            ),
        ):
            create_snapshot(fossil_repo_obj.pk)

        assert FossilSnapshot.objects.filter(repository=fossil_repo_obj).count() == 0

    def test_skips_duplicate_hash(self, fossil_repo_obj, tmp_path, admin_user):
        """If latest snapshot has the same hash, no new snapshot is created."""
        import hashlib

        from fossil.tasks import create_snapshot

        fossil_file = tmp_path / "test.fossil"
        data = b"SAME DATA TWICE"
        fossil_file.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()

        # Create an existing snapshot with the same hash
        FossilSnapshot.objects.create(
            repository=fossil_repo_obj,
            file_size_bytes=len(data),
            fossil_hash=sha,
            note="previous",
            created_by=admin_user,
        )

        with (
            patch("constance.config", self._mock_config(store_in_db=True)),
            patch.object(type(fossil_repo_obj), "full_path", new_callable=PropertyMock, return_value=fossil_file),
            _disk_exists,
        ):
            create_snapshot(fossil_repo_obj.pk, note="duplicate check")

        # Still only one snapshot
        assert FossilSnapshot.objects.filter(repository=fossil_repo_obj).count() == 1


# ===================================================================
# fossil/tasks.py -- check_upstream_updates
# ===================================================================


@pytest.mark.django_db
class TestCheckUpstreamUpdates:
    """Test the check_upstream periodic task."""

    def test_pulls_and_updates_metadata_when_artifacts_received(self, fossil_repo_obj):
        """When upstream has new artifacts, metadata is updated after pull."""
        from fossil.tasks import check_upstream_updates

        # Give the repo a remote URL
        fossil_repo_obj.remote_url = "https://fossil.example.com/repo"
        fossil_repo_obj.save(update_fields=["remote_url"])

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True
        cli_mock.pull.return_value = {"success": True, "artifacts_received": 5, "message": "received: 5"}

        timeline_entry = _make_timeline_entry()
        reader_mock = _make_reader_mock(
            get_checkin_count=50,
            get_timeline=[timeline_entry],
        )

        fake_stat = MagicMock()
        fake_stat.st_size = 200000

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
            patch("fossil.reader.FossilReader", reader_mock),
            patch.object(type(fossil_repo_obj), "full_path", new_callable=PropertyMock) as mock_path,
        ):
            mock_path.return_value = MagicMock()
            mock_path.return_value.stat.return_value = fake_stat

            check_upstream_updates()

        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.upstream_artifacts_available == 5
        assert fossil_repo_obj.checkin_count == 50
        assert fossil_repo_obj.last_sync_at is not None
        assert fossil_repo_obj.file_size_bytes == 200000

    def test_zero_artifacts_resets_counter(self, fossil_repo_obj):
        """When pull returns zero artifacts, upstream count is reset."""
        from fossil.tasks import check_upstream_updates

        fossil_repo_obj.remote_url = "https://fossil.example.com/repo"
        fossil_repo_obj.upstream_artifacts_available = 10
        fossil_repo_obj.save(update_fields=["remote_url", "upstream_artifacts_available"])

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True
        cli_mock.pull.return_value = {"success": True, "artifacts_received": 0, "message": "received: 0"}

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
        ):
            check_upstream_updates()

        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.upstream_artifacts_available == 0
        assert fossil_repo_obj.last_sync_at is not None

    def test_skips_when_fossil_not_available(self, fossil_repo_obj):
        """When fossil binary is not available, task returns early."""
        from fossil.tasks import check_upstream_updates

        fossil_repo_obj.remote_url = "https://fossil.example.com/repo"
        fossil_repo_obj.save(update_fields=["remote_url"])

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = False

        with patch("fossil.cli.FossilCLI", return_value=cli_mock):
            check_upstream_updates()

        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.last_sync_at is None

    def test_handles_pull_exception(self, fossil_repo_obj):
        """If pull raises an exception, the task logs and continues."""
        from fossil.tasks import check_upstream_updates

        fossil_repo_obj.remote_url = "https://fossil.example.com/repo"
        fossil_repo_obj.save(update_fields=["remote_url"])

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True
        cli_mock.pull.side_effect = Exception("network error")

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
        ):
            # Should not raise
            check_upstream_updates()

    def test_skips_repos_without_remote_url(self, fossil_repo_obj):
        """Repos with empty remote_url are excluded from the queryset."""
        from fossil.tasks import check_upstream_updates

        # fossil_repo_obj.remote_url is "" by default
        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True

        with patch("fossil.cli.FossilCLI", return_value=cli_mock):
            check_upstream_updates()

        # pull should never be called since no repos have remote_url
        cli_mock.pull.assert_not_called()


# ===================================================================
# fossil/tasks.py -- run_git_sync
# ===================================================================


@pytest.mark.django_db
class TestRunGitSync:
    """Test the run_git_sync task for Git mirror operations."""

    @staticmethod
    def _git_config():
        cfg = MagicMock()
        cfg.GIT_MIRROR_DIR = "/tmp/git-mirrors"
        return cfg

    def test_successful_sync_creates_log(self, mirror, fossil_repo_obj):
        """A successful git export updates the mirror and creates a success log."""
        from fossil.tasks import run_git_sync

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True
        cli_mock.git_export.return_value = {"success": True, "message": "Exported 10 commits"}

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
            patch("constance.config", self._git_config()),
        ):
            run_git_sync(mirror_id=mirror.pk)

        log = SyncLog.objects.get(mirror=mirror)
        assert log.status == "success"
        assert log.triggered_by == "manual"
        assert log.completed_at is not None

        mirror.refresh_from_db()
        assert mirror.last_sync_status == "success"
        assert mirror.total_syncs == 1

    def test_failed_sync_records_failure(self, mirror, fossil_repo_obj):
        """A failed git export records the failure in log and mirror."""
        from fossil.tasks import run_git_sync

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True
        cli_mock.git_export.return_value = {"success": False, "message": "Push rejected by remote"}

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
            patch("constance.config", self._git_config()),
        ):
            run_git_sync(mirror_id=mirror.pk)

        log = SyncLog.objects.get(mirror=mirror)
        assert log.status == "failed"

        mirror.refresh_from_db()
        assert mirror.last_sync_status == "failed"

    def test_exception_during_sync_creates_failed_log(self, mirror, fossil_repo_obj):
        """An unexpected exception during sync records a failed log."""
        from fossil.tasks import run_git_sync

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True
        cli_mock.git_export.side_effect = RuntimeError("subprocess crash")

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
            patch("constance.config", self._git_config()),
        ):
            run_git_sync(mirror_id=mirror.pk)

        log = SyncLog.objects.get(mirror=mirror)
        assert log.status == "failed"
        assert "Unexpected error" in log.message

    def test_credential_redacted_from_log(self, mirror, fossil_repo_obj):
        """Auth credentials must not appear in sync log messages."""
        from fossil.tasks import run_git_sync

        token = mirror.auth_credential
        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True
        cli_mock.git_export.return_value = {"success": True, "message": f"Push to remote with {token} auth"}

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
            patch("constance.config", self._git_config()),
        ):
            run_git_sync(mirror_id=mirror.pk)

        log = SyncLog.objects.get(mirror=mirror)
        assert token not in log.message
        assert "[REDACTED]" in log.message

    def test_skips_when_fossil_not_available(self, mirror):
        """When fossil binary is not available, task returns early."""
        from fossil.tasks import run_git_sync

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = False

        with patch("fossil.cli.FossilCLI", return_value=cli_mock):
            run_git_sync(mirror_id=mirror.pk)

        assert SyncLog.objects.count() == 0

    def test_skips_disabled_mirrors(self, fossil_repo_obj, admin_user):
        """Mirrors with sync_mode='disabled' are excluded."""
        from fossil.tasks import run_git_sync

        disabled_mirror = GitMirror.objects.create(
            repository=fossil_repo_obj,
            git_remote_url="https://github.com/test/disabled.git",
            sync_mode="disabled",
            created_by=admin_user,
        )

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
            patch("constance.config", self._git_config()),
        ):
            run_git_sync()

        assert SyncLog.objects.filter(mirror=disabled_mirror).count() == 0

    def test_chains_ticket_and_wiki_sync_when_enabled(self, mirror, fossil_repo_obj):
        """Successful sync chains ticket/wiki sync tasks when enabled."""
        from fossil.tasks import run_git_sync

        mirror.sync_tickets = True
        mirror.sync_wiki = True
        mirror.save(update_fields=["sync_tickets", "sync_wiki"])

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True
        cli_mock.git_export.return_value = {"success": True, "message": "ok"}

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
            patch("constance.config", self._git_config()),
            patch("fossil.tasks.sync_tickets_to_github") as mock_tickets,
            patch("fossil.tasks.sync_wiki_to_github") as mock_wiki,
        ):
            run_git_sync(mirror_id=mirror.pk)

        mock_tickets.delay.assert_called_once_with(mirror.id)
        mock_wiki.delay.assert_called_once_with(mirror.id)

    def test_schedule_triggered_by(self, mirror, fossil_repo_obj):
        """When called without mirror_id, triggered_by is 'schedule'."""
        from fossil.tasks import run_git_sync

        cli_mock = MagicMock()
        cli_mock.is_available.return_value = True
        cli_mock.git_export.return_value = {"success": True, "message": "ok"}

        with (
            _disk_exists,
            patch("fossil.cli.FossilCLI", return_value=cli_mock),
            patch("constance.config", self._git_config()),
        ):
            run_git_sync()  # no mirror_id

        log = SyncLog.objects.get(mirror=mirror)
        assert log.triggered_by == "schedule"


# ===================================================================
# fossil/tasks.py -- dispatch_notifications
# ===================================================================


@pytest.mark.django_db
class TestDispatchNotifications:
    """Test the dispatch_notifications periodic task."""

    def test_creates_notifications_for_recent_events(self, fossil_repo_obj, sample_project, admin_user):
        """Recent timeline events create notifications for project watchers."""
        from fossil.tasks import dispatch_notifications

        # Create a watcher
        ProjectWatch.objects.create(
            project=sample_project,
            user=admin_user,
            email_enabled=True,
            created_by=admin_user,
        )
        NotificationPreference.objects.create(user=admin_user, delivery_mode="immediate")

        recent_entry = _make_timeline_entry(
            event_type="ci",
            comment="Added new feature",
            user="dev",
        )

        reader_mock = _make_reader_mock(get_timeline=[recent_entry])

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("django.core.mail.send_mail"),
            patch("django.template.loader.render_to_string", return_value="<html>test</html>"),
        ):
            dispatch_notifications()

        notif = Notification.objects.filter(user=admin_user, project=sample_project).first()
        assert notif is not None
        assert "Added new feature" in notif.title or "dev" in notif.title

    def test_skips_when_no_watched_projects(self, fossil_repo_obj):
        """Task returns early when nobody is watching any projects."""
        from fossil.tasks import dispatch_notifications

        # No watches exist, so task should complete immediately
        dispatch_notifications()
        assert Notification.objects.count() == 0

    def test_skips_repo_not_on_disk(self, fossil_repo_obj, sample_project, admin_user):
        """Repos that don't exist on disk are skipped."""
        from fossil.tasks import dispatch_notifications

        ProjectWatch.objects.create(
            project=sample_project,
            user=admin_user,
            email_enabled=True,
            created_by=admin_user,
        )

        with patch(
            "fossil.models.FossilRepository.exists_on_disk",
            new_callable=lambda: property(lambda self: False),
        ):
            dispatch_notifications()

        assert Notification.objects.count() == 0

    def test_handles_reader_exception(self, fossil_repo_obj, sample_project, admin_user):
        """Reader exceptions are caught and logged per-repo."""
        from fossil.tasks import dispatch_notifications

        ProjectWatch.objects.create(
            project=sample_project,
            user=admin_user,
            email_enabled=True,
            created_by=admin_user,
        )

        reader_mock = MagicMock(side_effect=Exception("corrupt db"))

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
        ):
            # Should not raise
            dispatch_notifications()


# ===================================================================
# fossil/tasks.py -- sync_tickets_to_github
# ===================================================================


@pytest.mark.django_db
class TestSyncTicketsToGithub:
    """Test the sync_tickets_to_github task."""

    def test_creates_new_github_issues(self, mirror, fossil_repo_obj):
        """Unsynced tickets create new GitHub issues with mappings."""
        from fossil.tasks import sync_tickets_to_github

        ticket = _make_ticket(uuid="new-ticket-uuid-001")
        detail = _make_ticket(uuid="new-ticket-uuid-001")

        reader_mock = _make_reader_mock(
            get_tickets=[ticket],
            get_ticket_detail=detail,
            get_ticket_comments=[],
        )

        gh_client_mock = MagicMock()
        gh_client_mock.create_issue.return_value = {"number": 42, "url": "https://github.com/test/42", "error": ""}

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient", return_value=gh_client_mock),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_tickets_to_github(mirror.pk)

        mapping = TicketSyncMapping.objects.get(mirror=mirror, fossil_ticket_uuid="new-ticket-uuid-001")
        assert mapping.github_issue_number == 42

        log = SyncLog.objects.get(mirror=mirror, triggered_by="ticket_sync")
        assert log.status == "success"
        assert "1 tickets" in log.message

    def test_updates_existing_github_issue(self, mirror, fossil_repo_obj):
        """Already-synced tickets with changed status update the existing issue."""
        from fossil.tasks import sync_tickets_to_github

        # Pre-existing mapping with old status
        TicketSyncMapping.objects.create(
            mirror=mirror,
            fossil_ticket_uuid="existing-ticket-001",
            github_issue_number=10,
            fossil_status="open",
        )

        ticket = _make_ticket(uuid="existing-ticket-001", status="closed")
        detail = _make_ticket(uuid="existing-ticket-001", status="closed")

        reader_mock = _make_reader_mock(
            get_tickets=[ticket],
            get_ticket_detail=detail,
            get_ticket_comments=[],
        )

        gh_client_mock = MagicMock()
        gh_client_mock.update_issue.return_value = {"success": True, "error": ""}

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient", return_value=gh_client_mock),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_tickets_to_github(mirror.pk)

        mapping = TicketSyncMapping.objects.get(mirror=mirror, fossil_ticket_uuid="existing-ticket-001")
        assert mapping.fossil_status == "closed"

        gh_client_mock.update_issue.assert_called_once()

    def test_skips_already_synced_same_status(self, mirror, fossil_repo_obj):
        """Tickets already synced with the same status are skipped."""
        from fossil.tasks import sync_tickets_to_github

        TicketSyncMapping.objects.create(
            mirror=mirror,
            fossil_ticket_uuid="synced-ticket-001",
            github_issue_number=5,
            fossil_status="open",
        )

        ticket = _make_ticket(uuid="synced-ticket-001", status="open")

        reader_mock = _make_reader_mock(get_tickets=[ticket])

        gh_client_mock = MagicMock()

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient", return_value=gh_client_mock),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_tickets_to_github(mirror.pk)

        # Neither create nor update called
        gh_client_mock.create_issue.assert_not_called()
        gh_client_mock.update_issue.assert_not_called()

    def test_returns_early_for_deleted_mirror(self):
        """Task exits gracefully when mirror doesn't exist."""
        from fossil.tasks import sync_tickets_to_github

        sync_tickets_to_github(99999)
        assert SyncLog.objects.count() == 0

    def test_returns_early_when_no_auth_token(self, mirror, fossil_repo_obj):
        """Task warns and exits when mirror has no auth_credential."""
        from fossil.tasks import sync_tickets_to_github

        mirror.auth_credential = ""
        mirror.save(update_fields=["auth_credential"])

        with (
            _disk_exists,
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_tickets_to_github(mirror.pk)

        # A log is not created because we return before SyncLog.objects.create
        assert SyncLog.objects.filter(mirror=mirror, triggered_by="ticket_sync").count() == 0

    def test_returns_early_when_url_not_parseable(self, mirror, fossil_repo_obj):
        """Task exits when git_remote_url can't be parsed to owner/repo."""
        from fossil.tasks import sync_tickets_to_github

        with (
            _disk_exists,
            patch("fossil.github_api.parse_github_repo", return_value=None),
        ):
            sync_tickets_to_github(mirror.pk)

        assert SyncLog.objects.filter(mirror=mirror, triggered_by="ticket_sync").count() == 0

    def test_handles_exception_during_sync(self, mirror, fossil_repo_obj):
        """Unexpected exceptions are caught and logged."""
        from fossil.tasks import sync_tickets_to_github

        reader_mock = MagicMock(side_effect=Exception("reader crash"))

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient"),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_tickets_to_github(mirror.pk)

        log = SyncLog.objects.get(mirror=mirror, triggered_by="ticket_sync")
        assert log.status == "failed"
        assert "Unexpected error" in log.message

    def test_create_issue_error_recorded(self, mirror, fossil_repo_obj):
        """When GitHub create_issue returns an error, it's recorded in the log."""
        from fossil.tasks import sync_tickets_to_github

        ticket = _make_ticket(uuid="fail-create-001")
        detail = _make_ticket(uuid="fail-create-001")

        reader_mock = _make_reader_mock(
            get_tickets=[ticket],
            get_ticket_detail=detail,
            get_ticket_comments=[],
        )

        gh_client_mock = MagicMock()
        gh_client_mock.create_issue.return_value = {"number": 0, "url": "", "error": "HTTP 403: Forbidden"}

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient", return_value=gh_client_mock),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_tickets_to_github(mirror.pk)

        log = SyncLog.objects.get(mirror=mirror, triggered_by="ticket_sync")
        assert log.status == "failed"
        assert "Errors" in log.message


# ===================================================================
# fossil/tasks.py -- sync_wiki_to_github
# ===================================================================


@pytest.mark.django_db
class TestSyncWikiToGithub:
    """Test the sync_wiki_to_github task."""

    def test_syncs_new_wiki_pages(self, mirror, fossil_repo_obj):
        """New wiki pages are pushed to GitHub and mappings created."""
        from fossil.tasks import sync_wiki_to_github

        page_listing = _make_wiki_page(name="Home", content="")
        full_page = _make_wiki_page(name="Home", content="# Home\nWelcome to the wiki.")

        reader_mock = _make_reader_mock(
            get_wiki_pages=[page_listing],
            get_wiki_page=full_page,
        )

        gh_client_mock = MagicMock()
        gh_client_mock.create_or_update_file.return_value = {"success": True, "sha": "abc123", "error": ""}

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient", return_value=gh_client_mock),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_wiki_to_github(mirror.pk)

        mapping = WikiSyncMapping.objects.get(mirror=mirror, fossil_page_name="Home")
        assert mapping.github_path == "wiki/Home.md"
        assert mapping.content_hash  # should be a sha256 hex string

        log = SyncLog.objects.get(mirror=mirror, triggered_by="wiki_sync")
        assert log.status == "success"
        assert "1 wiki pages" in log.message

    def test_updates_existing_page_mapping(self, mirror, fossil_repo_obj):
        """Changed content updates the existing mapping hash."""
        from fossil.github_api import content_hash
        from fossil.tasks import sync_wiki_to_github

        old_hash = content_hash("old content")
        WikiSyncMapping.objects.create(
            mirror=mirror,
            fossil_page_name="Changelog",
            content_hash=old_hash,
            github_path="wiki/Changelog.md",
        )

        page_listing = _make_wiki_page(name="Changelog", content="")
        full_page = _make_wiki_page(name="Changelog", content="# Changelog\nv2.0 release")

        reader_mock = _make_reader_mock(
            get_wiki_pages=[page_listing],
            get_wiki_page=full_page,
        )

        gh_client_mock = MagicMock()
        gh_client_mock.create_or_update_file.return_value = {"success": True, "sha": "def456", "error": ""}

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient", return_value=gh_client_mock),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_wiki_to_github(mirror.pk)

        mapping = WikiSyncMapping.objects.get(mirror=mirror, fossil_page_name="Changelog")
        new_hash = content_hash("# Changelog\nv2.0 release")
        assert mapping.content_hash == new_hash

    def test_skips_unchanged_content(self, mirror, fossil_repo_obj):
        """Pages with unchanged content hash are not re-pushed."""
        from fossil.github_api import content_hash
        from fossil.tasks import sync_wiki_to_github

        content = "# Home\nSame content."
        WikiSyncMapping.objects.create(
            mirror=mirror,
            fossil_page_name="Home",
            content_hash=content_hash(content),
            github_path="wiki/Home.md",
        )

        page_listing = _make_wiki_page(name="Home", content="")
        full_page = _make_wiki_page(name="Home", content=content)

        reader_mock = _make_reader_mock(
            get_wiki_pages=[page_listing],
            get_wiki_page=full_page,
        )

        gh_client_mock = MagicMock()

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient", return_value=gh_client_mock),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_wiki_to_github(mirror.pk)

        gh_client_mock.create_or_update_file.assert_not_called()

    def test_skips_empty_page_content(self, mirror, fossil_repo_obj):
        """Pages with empty content after stripping are skipped."""
        from fossil.tasks import sync_wiki_to_github

        page_listing = _make_wiki_page(name="Empty", content="")
        full_page = _make_wiki_page(name="Empty", content="   \n  ")

        reader_mock = _make_reader_mock(
            get_wiki_pages=[page_listing],
            get_wiki_page=full_page,
        )

        gh_client_mock = MagicMock()

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient", return_value=gh_client_mock),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_wiki_to_github(mirror.pk)

        gh_client_mock.create_or_update_file.assert_not_called()

    def test_returns_early_for_deleted_mirror(self):
        """Task exits for nonexistent mirror."""
        from fossil.tasks import sync_wiki_to_github

        sync_wiki_to_github(99999)
        assert SyncLog.objects.count() == 0

    def test_returns_early_when_no_auth_token(self, mirror, fossil_repo_obj):
        """Task exits when no auth token available."""
        from fossil.tasks import sync_wiki_to_github

        mirror.auth_credential = ""
        mirror.save(update_fields=["auth_credential"])

        with (
            _disk_exists,
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_wiki_to_github(mirror.pk)

        assert SyncLog.objects.filter(mirror=mirror, triggered_by="wiki_sync").count() == 0

    def test_handles_github_api_error(self, mirror, fossil_repo_obj):
        """GitHub API errors are recorded in the log."""
        from fossil.tasks import sync_wiki_to_github

        page_listing = _make_wiki_page(name="Failing", content="")
        full_page = _make_wiki_page(name="Failing", content="# Oops")

        reader_mock = _make_reader_mock(
            get_wiki_pages=[page_listing],
            get_wiki_page=full_page,
        )

        gh_client_mock = MagicMock()
        gh_client_mock.create_or_update_file.return_value = {"success": False, "sha": "", "error": "HTTP 500"}

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient", return_value=gh_client_mock),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_wiki_to_github(mirror.pk)

        log = SyncLog.objects.get(mirror=mirror, triggered_by="wiki_sync")
        assert log.status == "failed"
        assert "Errors" in log.message

    def test_handles_exception_during_sync(self, mirror, fossil_repo_obj):
        """Unexpected exceptions are caught and recorded."""
        from fossil.tasks import sync_wiki_to_github

        reader_mock = MagicMock(side_effect=Exception("reader crash"))

        with (
            _disk_exists,
            patch("fossil.reader.FossilReader", reader_mock),
            patch("fossil.github_api.GitHubClient"),
            patch("fossil.github_api.parse_github_repo", return_value=("testorg", "testrepo")),
        ):
            sync_wiki_to_github(mirror.pk)

        log = SyncLog.objects.get(mirror=mirror, triggered_by="wiki_sync")
        assert log.status == "failed"
        assert "Unexpected error" in log.message


# ===================================================================
# fossil/tasks.py -- dispatch_webhook (additional edge cases)
# ===================================================================


@pytest.mark.django_db
class TestDispatchWebhookEdgeCases:
    """Edge cases for the dispatch_webhook task not covered by test_webhooks.py."""

    def test_unsafe_url_blocked_at_dispatch_time(self, webhook):
        """URLs that fail safety check at dispatch are blocked and logged."""
        from fossil.tasks import dispatch_webhook

        with patch("core.url_validation.is_safe_outbound_url", return_value=(False, "Private IP detected")):
            dispatch_webhook.apply(args=[webhook.pk, "checkin", {"hash": "abc"}])

        delivery = WebhookDelivery.objects.get(webhook=webhook)
        assert delivery.success is False
        assert delivery.response_status == 0
        assert "Blocked" in delivery.response_body
        assert "Private IP" in delivery.response_body

    def test_request_exception_creates_delivery_and_retries(self, webhook):
        """Network errors create a delivery record and trigger retry."""
        import requests as req

        from fossil.tasks import dispatch_webhook

        with (
            patch("core.url_validation.is_safe_outbound_url", return_value=(True, "")),
            patch("requests.post", side_effect=req.ConnectionError("refused")),
        ):
            dispatch_webhook.apply(args=[webhook.pk, "ticket", {"id": "123"}])

        delivery = WebhookDelivery.objects.filter(webhook=webhook).first()
        assert delivery is not None
        assert delivery.success is False
        assert delivery.response_status == 0
        assert "refused" in delivery.response_body


# ===================================================================
# accounts/views.py -- _sanitize_ssh_key
# ===================================================================


class TestSanitizeSSHKey:
    """Unit tests for SSH key validation (no DB needed)."""

    def test_rejects_key_with_newlines(self):
        from accounts.views import _sanitize_ssh_key

        result, error = _sanitize_ssh_key("ssh-ed25519 AAAA key1\nssh-rsa BBBB key2")
        assert result is None
        assert "Newlines" in error

    def test_rejects_key_with_carriage_return(self):
        from accounts.views import _sanitize_ssh_key

        result, error = _sanitize_ssh_key("ssh-ed25519 AAAA key1\rssh-rsa BBBB key2")
        assert result is None
        assert "Newlines" in error

    def test_rejects_key_with_null_byte(self):
        from accounts.views import _sanitize_ssh_key

        result, error = _sanitize_ssh_key("ssh-ed25519 AAAA\x00inject")
        assert result is None
        assert "null bytes" in error

    def test_rejects_empty_key(self):
        from accounts.views import _sanitize_ssh_key

        result, error = _sanitize_ssh_key("   ")
        assert result is None
        assert "empty" in error.lower()

    def test_rejects_wrong_part_count(self):
        from accounts.views import _sanitize_ssh_key

        result, error = _sanitize_ssh_key("ssh-ed25519")
        assert result is None
        assert "format" in error.lower()

    def test_rejects_too_many_parts(self):
        from accounts.views import _sanitize_ssh_key

        result, error = _sanitize_ssh_key("ssh-ed25519 AAAA comment extra-part")
        assert result is None
        assert "format" in error.lower()

    def test_rejects_unsupported_key_type(self):
        from accounts.views import _sanitize_ssh_key

        result, error = _sanitize_ssh_key("ssh-unknown AAAA comment")
        assert result is None
        assert "Unsupported" in error

    def test_rejects_bad_base64(self):
        from accounts.views import _sanitize_ssh_key

        result, error = _sanitize_ssh_key("ssh-ed25519 !!!invalid comment")
        assert result is None
        assert "encoding" in error.lower()

    def test_accepts_valid_ed25519_key(self):
        from accounts.views import _sanitize_ssh_key

        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyDataHere= user@host"
        result, error = _sanitize_ssh_key(key)
        assert result == key
        assert error == ""

    def test_accepts_valid_rsa_key(self):
        from accounts.views import _sanitize_ssh_key

        key = "ssh-rsa AAAAB3NzaC1yc2EAAAAFakeBase64Data== user@host"
        result, error = _sanitize_ssh_key(key)
        assert result == key
        assert error == ""

    def test_accepts_ecdsa_key(self):
        from accounts.views import _sanitize_ssh_key

        key = "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTY= user@host"
        result, error = _sanitize_ssh_key(key)
        assert result == key
        assert error == ""

    def test_strips_whitespace(self):
        from accounts.views import _sanitize_ssh_key

        key = "  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake=  "
        result, error = _sanitize_ssh_key(key)
        assert result is not None
        assert result == key.strip()


# ===================================================================
# accounts/views.py -- _verify_turnstile
# ===================================================================


class TestVerifyTurnstile:
    """Unit tests for Turnstile CAPTCHA verification."""

    @staticmethod
    def _turnstile_config(secret_key=""):
        cfg = MagicMock()
        cfg.TURNSTILE_SECRET_KEY = secret_key
        return cfg

    def test_returns_false_when_no_secret_key(self):
        from accounts.views import _verify_turnstile

        with patch("constance.config", self._turnstile_config(secret_key="")):
            assert _verify_turnstile("some-token", "1.2.3.4") is False

    def test_returns_true_on_success(self):
        from accounts.views import _verify_turnstile

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}

        with (
            patch("constance.config", self._turnstile_config(secret_key="secret-key")),
            patch("requests.post", return_value=mock_resp),
        ):
            assert _verify_turnstile("valid-token", "1.2.3.4") is True

    def test_returns_false_on_failed_verification(self):
        from accounts.views import _verify_turnstile

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": False}

        with (
            patch("constance.config", self._turnstile_config(secret_key="secret-key")),
            patch("requests.post", return_value=mock_resp),
        ):
            assert _verify_turnstile("bad-token", "1.2.3.4") is False

    def test_returns_false_on_network_error(self):
        from accounts.views import _verify_turnstile

        with (
            patch("constance.config", self._turnstile_config(secret_key="secret-key")),
            patch("requests.post", side_effect=Exception("connection refused")),
        ):
            assert _verify_turnstile("token", "1.2.3.4") is False


# ===================================================================
# accounts/views.py -- Login Turnstile flow
# ===================================================================


def _login_turnstile_config():
    cfg = MagicMock()
    cfg.TURNSTILE_ENABLED = True
    cfg.TURNSTILE_SITE_KEY = "site-key-123"
    cfg.TURNSTILE_SECRET_KEY = "secret-key"
    return cfg


@pytest.mark.django_db
class TestLoginTurnstile:
    """Test login view with Turnstile CAPTCHA enabled."""

    def test_turnstile_error_rerenders_form(self, client, admin_user):
        """When Turnstile fails, the login form is re-rendered with error."""
        with (
            patch("constance.config", _login_turnstile_config()),
            patch("accounts.views._verify_turnstile", return_value=False),
        ):
            response = client.post(
                "/auth/login/",
                {"username": "admin", "password": "testpass123", "cf-turnstile-response": "bad-token"},
            )

        assert response.status_code == 200
        assert b"login" in response.content.lower()

    def test_turnstile_context_passed_to_template(self, client):
        """When Turnstile is enabled, context includes turnstile_enabled and site_key."""
        with patch("constance.config", _login_turnstile_config()):
            response = client.get("/auth/login/")

        assert response.status_code == 200
        assert response.context["turnstile_enabled"] is True
        assert response.context["turnstile_site_key"] == "site-key-123"


# ===================================================================
# accounts/views.py -- SSH key management
# ===================================================================


@pytest.mark.django_db
class TestSSHKeyViews:
    """Test SSH key list, add, and delete views."""

    def test_list_ssh_keys(self, admin_client, admin_user):
        response = admin_client.get("/auth/ssh-keys/")
        assert response.status_code == 200

    def test_add_valid_ssh_key(self, admin_client, admin_user):
        """Adding a valid SSH key creates the record and regenerates authorized_keys."""
        from fossil.user_keys import UserSSHKey

        with patch("accounts.views._regenerate_authorized_keys"):
            response = admin_client.post(
                "/auth/ssh-keys/",
                {
                    "title": "Work Laptop",
                    "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyDataHere= user@host",
                },
            )

        assert response.status_code == 302  # redirect after success
        key = UserSSHKey.objects.get(user=admin_user, title="Work Laptop")
        assert key.key_type == "ed25519"
        assert key.fingerprint  # SHA256 computed

    def test_add_invalid_ssh_key_shows_error(self, admin_client, admin_user):
        """Adding an invalid SSH key shows an error message."""
        response = admin_client.post(
            "/auth/ssh-keys/",
            {
                "title": "Bad Key",
                "public_key": "not-a-real-key",
            },
        )

        assert response.status_code == 200  # re-renders form

    def test_add_ssh_key_with_injection_newline(self, admin_client, admin_user):
        """Keys with newlines are rejected (injection prevention)."""
        from fossil.user_keys import UserSSHKey

        response = admin_client.post(
            "/auth/ssh-keys/",
            {
                "title": "Injected Key",
                "public_key": "ssh-ed25519 AAAA key1\nssh-rsa BBBB key2",
            },
        )

        assert response.status_code == 200
        assert UserSSHKey.objects.filter(user=admin_user).count() == 0

    def test_delete_ssh_key(self, admin_client, admin_user):
        """Deleting an SSH key soft-deletes it and regenerates authorized_keys."""
        from fossil.user_keys import UserSSHKey

        key = UserSSHKey.objects.create(
            user=admin_user,
            title="Delete Me",
            public_key="ssh-ed25519 AAAA= test",
            created_by=admin_user,
        )

        with patch("accounts.views._regenerate_authorized_keys"):
            response = admin_client.post(f"/auth/ssh-keys/{key.pk}/delete/")

        assert response.status_code == 302
        key.refresh_from_db()
        assert key.deleted_at is not None

    def test_delete_ssh_key_htmx(self, admin_client, admin_user):
        """HTMX delete returns HX-Redirect header."""
        from fossil.user_keys import UserSSHKey

        key = UserSSHKey.objects.create(
            user=admin_user,
            title="HX Delete",
            public_key="ssh-ed25519 AAAA= test",
            created_by=admin_user,
        )

        with patch("accounts.views._regenerate_authorized_keys"):
            response = admin_client.post(
                f"/auth/ssh-keys/{key.pk}/delete/",
                HTTP_HX_REQUEST="true",
            )

        assert response.status_code == 200
        assert response["HX-Redirect"] == "/auth/ssh-keys/"

    def test_delete_other_users_key_404(self, admin_client, viewer_user, admin_user):
        """Cannot delete another user's SSH key."""
        from fossil.user_keys import UserSSHKey

        key = UserSSHKey.objects.create(
            user=viewer_user,
            title="Viewer Key",
            public_key="ssh-ed25519 AAAA= test",
            created_by=viewer_user,
        )

        response = admin_client.post(f"/auth/ssh-keys/{key.pk}/delete/")
        assert response.status_code == 404

    def test_ssh_keys_require_login(self, client):
        response = client.get("/auth/ssh-keys/")
        assert response.status_code == 302
        assert "/auth/login/" in response.url


# ===================================================================
# accounts/views.py -- Notification preferences HTMX
# ===================================================================


@pytest.mark.django_db
class TestNotificationPreferencesHTMX:
    """Test the HTMX return path for notification preferences."""

    def test_post_htmx_returns_hx_redirect(self, admin_client, admin_user):
        """HTMX POST returns 200 with HX-Redirect header instead of 302."""
        NotificationPreference.objects.create(user=admin_user)

        response = admin_client.post(
            "/auth/notifications/",
            {"delivery_mode": "weekly"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert response["HX-Redirect"] == "/auth/notifications/"


# ===================================================================
# accounts/views.py -- _parse_key_type and _compute_fingerprint
# ===================================================================


class TestParseKeyType:
    """Unit tests for SSH key type parsing helper."""

    def test_ed25519(self):
        from accounts.views import _parse_key_type

        assert _parse_key_type("ssh-ed25519 AAAA") == "ed25519"

    def test_rsa(self):
        from accounts.views import _parse_key_type

        assert _parse_key_type("ssh-rsa AAAA") == "rsa"

    def test_ecdsa_256(self):
        from accounts.views import _parse_key_type

        assert _parse_key_type("ecdsa-sha2-nistp256 AAAA") == "ecdsa"

    def test_ecdsa_384(self):
        from accounts.views import _parse_key_type

        assert _parse_key_type("ecdsa-sha2-nistp384 AAAA") == "ecdsa"

    def test_dsa(self):
        from accounts.views import _parse_key_type

        assert _parse_key_type("ssh-dss AAAA") == "dsa"

    def test_unknown_type(self):
        from accounts.views import _parse_key_type

        assert _parse_key_type("custom-type AAAA") == "custom-type"

    def test_empty_string(self):
        from accounts.views import _parse_key_type

        assert _parse_key_type("") == ""


class TestComputeFingerprint:
    """Unit tests for SSH key fingerprint computation."""

    def test_computes_sha256_fingerprint(self):
        from accounts.views import _compute_fingerprint

        # Valid base64 key data
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyDataHere= user@host"
        result = _compute_fingerprint(key)
        assert result.startswith("SHA256:")

    def test_invalid_base64_returns_empty(self):
        from accounts.views import _compute_fingerprint

        key = "ssh-ed25519 !!!notbase64 user@host"
        result = _compute_fingerprint(key)
        assert result == ""

    def test_single_part_returns_empty(self):
        from accounts.views import _compute_fingerprint

        result = _compute_fingerprint("onlyonepart")
        assert result == ""


# ===================================================================
# accounts/views.py -- profile_token_create scopes edge cases
# ===================================================================


@pytest.mark.django_db
class TestProfileTokenCreateEdgeCases:
    """Additional edge cases for token creation."""

    def test_create_admin_scope_token(self, admin_client, admin_user):
        """Admin scope is a valid scope."""
        from accounts.models import PersonalAccessToken

        response = admin_client.post(
            "/auth/profile/tokens/create/",
            {"name": "Admin Token", "scopes": "read,write,admin"},
        )
        assert response.status_code == 200
        token = PersonalAccessToken.objects.get(user=admin_user, name="Admin Token")
        assert "admin" in token.scopes
        assert "read" in token.scopes
        assert "write" in token.scopes

    def test_create_token_mixed_valid_invalid_scopes(self, admin_client, admin_user):
        """Invalid scopes are filtered out, valid ones kept."""
        from accounts.models import PersonalAccessToken

        admin_client.post(
            "/auth/profile/tokens/create/",
            {"name": "Mixed Scopes", "scopes": "read,destroy,write,hack"},
        )
        token = PersonalAccessToken.objects.get(user=admin_user, name="Mixed Scopes")
        assert token.scopes == "read,write"

    def test_create_token_whitespace_scopes(self, admin_client, admin_user):
        """Scopes with extra whitespace are handled correctly."""
        from accounts.models import PersonalAccessToken

        admin_client.post(
            "/auth/profile/tokens/create/",
            {"name": "Whitespace", "scopes": " read , write "},
        )
        token = PersonalAccessToken.objects.get(user=admin_user, name="Whitespace")
        assert token.scopes == "read,write"

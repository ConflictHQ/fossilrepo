"""Celery tasks for Fossil repository management."""

import hashlib
import logging

from celery import shared_task
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


@shared_task(name="fossil.sync_metadata")
def sync_repository_metadata():
    """Update metadata for all FossilRepository records from disk."""
    from fossil.models import FossilRepository
    from fossil.reader import FossilReader

    for repo in FossilRepository.objects.all():
        if not repo.exists_on_disk:
            continue
        try:
            repo.file_size_bytes = repo.full_path.stat().st_size
            with FossilReader(repo.full_path) as reader:
                repo.checkin_count = reader.get_checkin_count()
                timeline = reader.get_timeline(limit=1)
                if timeline:
                    repo.last_checkin_at = timeline[0].timestamp
                repo.fossil_project_code = reader.get_project_code()
            repo.save(update_fields=["file_size_bytes", "checkin_count", "last_checkin_at", "fossil_project_code", "updated_at", "version"])
        except Exception:
            logger.exception("Failed to sync metadata for %s", repo.filename)


@shared_task(name="fossil.create_snapshot")
def create_snapshot(repository_id: int, note: str = ""):
    """Create a FossilSnapshot if FOSSIL_STORE_IN_DB is enabled."""
    from constance import config

    if not config.FOSSIL_STORE_IN_DB:
        return

    from fossil.models import FossilRepository, FossilSnapshot

    try:
        repo = FossilRepository.objects.get(pk=repository_id)
    except FossilRepository.DoesNotExist:
        return

    if not repo.exists_on_disk:
        return

    data = repo.full_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()

    # Skip if latest snapshot has same hash
    latest = repo.snapshots.first()
    if latest and latest.fossil_hash == sha:
        return

    snapshot = FossilSnapshot(
        repository=repo,
        file_size_bytes=len(data),
        fossil_hash=sha,
        note=note,
        created_by=repo.created_by,
    )
    snapshot.file.save(f"{repo.filename}_{sha[:8]}.fossil", ContentFile(data), save=True)
    logger.info("Created snapshot for %s (hash: %s)", repo.filename, sha[:8])


@shared_task(name="fossil.check_upstream")
def check_upstream_updates():
    """Check all repos with remote URLs for available updates."""
    from fossil.cli import FossilCLI
    from fossil.models import FossilRepository

    cli = FossilCLI()
    if not cli.is_available():
        return

    from django.utils import timezone

    for repo in FossilRepository.objects.exclude(remote_url=""):
        if not repo.exists_on_disk:
            continue
        try:
            result = cli.pull(repo.full_path)
            if result["success"] and result["artifacts_received"] > 0:
                repo.upstream_artifacts_available = result["artifacts_received"]
                repo.last_sync_at = timezone.now()
                # Update metadata after pull
                from fossil.reader import FossilReader

                with FossilReader(repo.full_path) as reader:
                    repo.checkin_count = reader.get_checkin_count()
                    timeline = reader.get_timeline(limit=1, event_type="ci")
                    if timeline:
                        repo.last_checkin_at = timeline[0].timestamp
                    repo.file_size_bytes = repo.full_path.stat().st_size
                repo.save(
                    update_fields=[
                        "upstream_artifacts_available",
                        "last_sync_at",
                        "checkin_count",
                        "last_checkin_at",
                        "file_size_bytes",
                        "updated_at",
                        "version",
                    ]
                )
                logger.info("Pulled %d artifacts for %s (new count: %d)", result["artifacts_received"], repo.filename, repo.checkin_count)
            else:
                repo.upstream_artifacts_available = 0
                repo.last_sync_at = timezone.now()
                repo.save(update_fields=["upstream_artifacts_available", "last_sync_at", "updated_at", "version"])
        except Exception:
            logger.exception("Failed to check upstream for %s", repo.filename)


@shared_task(name="fossil.git_sync")
def run_git_sync(mirror_id: int | None = None):
    """Run Git export/push for configured mirrors."""
    from pathlib import Path

    from constance import config
    from django.utils import timezone

    from fossil.cli import FossilCLI
    from fossil.sync_models import GitMirror, SyncLog

    cli = FossilCLI()
    if not cli.is_available():
        return

    mirrors = GitMirror.objects.filter(deleted_at__isnull=True).exclude(sync_mode="disabled")
    if mirror_id:
        mirrors = mirrors.filter(pk=mirror_id)

    mirror_dir = Path(config.GIT_MIRROR_DIR)

    for mirror in mirrors:
        repo = mirror.repository
        if not repo.exists_on_disk:
            continue

        log = SyncLog.objects.create(mirror=mirror, triggered_by="schedule" if not mirror_id else "manual")

        try:
            # Ensure default user
            cli.ensure_default_user(repo.full_path)

            # Git export directory for this mirror
            export_dir = mirror_dir / f"{repo.filename.replace('.fossil', '')}-git"

            # Build autopush URL with credentials if token auth
            push_url = mirror.git_remote_url
            if mirror.auth_method == "token" and mirror.auth_credential and push_url.startswith("https://"):
                push_url = push_url.replace("https://", f"https://{mirror.auth_credential}@")

            result = cli.git_export(repo.full_path, export_dir, autopush_url=push_url)

            log.status = "success" if result["success"] else "failed"
            log.message = result["message"]
            log.completed_at = timezone.now()
            log.save()

            mirror.last_sync_at = timezone.now()
            mirror.last_sync_status = log.status
            mirror.last_sync_message = result["message"][:500]
            mirror.total_syncs += 1
            mirror.save(
                update_fields=[
                    "last_sync_at",
                    "last_sync_status",
                    "last_sync_message",
                    "total_syncs",
                    "updated_at",
                    "version",
                ]
            )

            if result["success"]:
                logger.info("Git sync success for %s → %s", repo.filename, mirror.git_remote_url)
            else:
                logger.warning("Git sync failed for %s: %s", repo.filename, result["message"][:200])

        except Exception:
            logger.exception("Git sync error for %s", repo.filename)
            log.status = "failed"
            log.message = "Unexpected error"
            log.completed_at = timezone.now()
            log.save()

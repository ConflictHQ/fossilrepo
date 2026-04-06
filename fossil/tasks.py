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

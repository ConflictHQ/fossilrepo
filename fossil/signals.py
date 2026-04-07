"""Auto-create FossilRepository when a Project is created."""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from projects.models import Project

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Project)
def create_fossil_repo(sender, instance, created, **kwargs):
    """When a new Project is created, create a FossilRepository record and init the .fossil file."""
    if not created:
        return

    from fossil.models import FossilRepository

    if FossilRepository.objects.filter(project=instance).exists():
        return

    filename = f"{instance.slug}.fossil"
    repo = FossilRepository.objects.create(
        project=instance,
        filename=filename,
        created_by=instance.created_by,
    )

    # Try to init the .fossil file on disk (skip if file already exists, e.g. from a clone)
    if not repo.full_path.exists():
        try:
            from fossil.cli import FossilCLI

            cli = FossilCLI()
            if cli.is_available():
                cli.init(repo.full_path)
                repo.file_size_bytes = repo.full_path.stat().st_size if repo.exists_on_disk else 0
                repo.save(update_fields=["file_size_bytes", "updated_at", "version"])
                logger.info("Created fossil repo: %s", repo.full_path)
            else:
                logger.warning("Fossil binary not available — repo record created but .fossil file not initialized")
        except Exception:
            logger.exception("Failed to init fossil repo: %s", filename)
    else:
        logger.info("Fossil file already exists, skipping init: %s", repo.full_path)

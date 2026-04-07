from pathlib import Path

from django.db import models

from core.models import ActiveManager, Tracking


class FossilRepository(Tracking):
    """Links a Project to its on-disk .fossil SQLite file."""

    project = models.OneToOneField("projects.Project", on_delete=models.CASCADE, related_name="fossil_repo")
    filename = models.CharField(max_length=255, unique=True, help_text="Filename relative to FOSSIL_DATA_DIR")
    file_size_bytes = models.BigIntegerField(default=0)
    fossil_project_code = models.CharField(max_length=40, blank=True, default="")
    last_checkin_at = models.DateTimeField(null=True, blank=True)
    checkin_count = models.PositiveIntegerField(default=0)

    # Remote sync
    remote_url = models.URLField(blank=True, default="", help_text="Upstream remote URL for sync")
    last_sync_at = models.DateTimeField(null=True, blank=True)
    upstream_artifacts_available = models.PositiveIntegerField(default=0, help_text="New artifacts available from upstream")

    # S3 tracking
    s3_key = models.CharField(max_length=500, blank=True, default="")
    s3_last_replicated_at = models.DateTimeField(null=True, blank=True)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["filename"]
        verbose_name = "Fossil Repository"
        verbose_name_plural = "Fossil Repositories"

    def __str__(self):
        return self.filename

    @property
    def full_path(self) -> Path:
        from constance import config

        return Path(config.FOSSIL_DATA_DIR) / self.filename

    @property
    def exists_on_disk(self) -> bool:
        return self.full_path.exists()


class FossilSnapshot(Tracking):
    """Binary snapshot of a .fossil file stored via Django's file storage."""

    repository = models.ForeignKey(FossilRepository, on_delete=models.CASCADE, related_name="snapshots")
    file = models.FileField(upload_to="fossil_snapshots/%Y/%m/")
    file_size_bytes = models.BigIntegerField(default=0)
    fossil_hash = models.CharField(max_length=64, blank=True, default="", help_text="SHA-256 of the .fossil file")
    note = models.CharField(max_length=200, blank=True, default="")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]
        get_latest_by = "created_at"

    def __str__(self):
        return f"{self.repository.filename} @ {self.created_at:%Y-%m-%d %H:%M}" if self.created_at else self.repository.filename


# Import related models so they're discoverable by Django
from fossil.forum import ForumPost  # noqa: E402, F401
from fossil.notifications import Notification, ProjectWatch  # noqa: E402, F401
from fossil.releases import Release, ReleaseAsset  # noqa: E402, F401
from fossil.sync_models import GitMirror, SSHKey, SyncLog  # noqa: E402, F401
from fossil.user_keys import UserSSHKey  # noqa: E402, F401
from fossil.webhooks import Webhook, WebhookDelivery  # noqa: E402, F401

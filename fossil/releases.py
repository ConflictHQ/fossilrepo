from django.db import models

from core.models import ActiveManager, Tracking


class Release(Tracking):
    """A tagged release for a Fossil repository with changelog and downloadable assets."""

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="releases")
    tag_name = models.CharField(max_length=200)  # e.g. "v1.0.0"
    name = models.CharField(max_length=300)  # e.g. "Version 1.0.0 -- Initial Release"
    body = models.TextField(blank=True, default="")  # Markdown changelog
    is_prerelease = models.BooleanField(default=False)
    is_draft = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    # Link to Fossil checkin if available
    checkin_uuid = models.CharField(max_length=64, blank=True, default="")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        unique_together = [("repository", "tag_name")]

    def __str__(self):
        return f"{self.tag_name}: {self.name}"


class ReleaseAsset(Tracking):
    """A downloadable file attached to a release (binary, tarball, etc.)."""

    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="assets")
    name = models.CharField(max_length=300)
    file = models.FileField(upload_to="release_assets/%Y/%m/")
    file_size_bytes = models.BigIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True, default="")
    download_count = models.PositiveIntegerField(default=0)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

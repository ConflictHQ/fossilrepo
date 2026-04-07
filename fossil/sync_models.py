"""Git mirror sync models for Fossil-to-Git synchronization."""

from django.db import models

from core.fields import EncryptedTextField
from core.models import ActiveManager, Tracking


class GitMirror(Tracking):
    """Configuration for syncing a Fossil repo to a Git remote."""

    class AuthMethod(models.TextChoices):
        SSH_KEY = "ssh", "SSH Key"
        TOKEN = "token", "Personal Access Token"
        OAUTH_GITHUB = "oauth_github", "GitHub OAuth"
        OAUTH_GITLAB = "oauth_gitlab", "GitLab OAuth"

    class SyncDirection(models.TextChoices):
        PUSH = "push", "Push (Fossil → Git)"
        PULL = "pull", "Pull (Git → Fossil)"
        BOTH = "both", "Bidirectional"

    class SyncMode(models.TextChoices):
        ON_CHANGE = "on_change", "On Change"
        SCHEDULED = "scheduled", "Scheduled"
        BOTH = "both", "Both"
        DISABLED = "disabled", "Disabled"

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="git_mirrors")
    git_remote_url = models.CharField(max_length=500, help_text="Git remote URL (SSH or HTTPS)")
    auth_method = models.CharField(max_length=20, choices=AuthMethod.choices, default=AuthMethod.TOKEN)
    auth_credential = EncryptedTextField(blank=True, default="", help_text="Token or key reference (encrypted at rest)")

    sync_direction = models.CharField(max_length=10, choices=SyncDirection.choices, default=SyncDirection.PUSH)
    sync_mode = models.CharField(max_length=20, choices=SyncMode.choices, default=SyncMode.SCHEDULED)
    sync_schedule = models.CharField(max_length=100, blank=True, default="*/15 * * * *", help_text="Cron expression for scheduled sync")

    # What to sync
    sync_code = models.BooleanField(default=True)
    sync_tickets = models.BooleanField(default=False, help_text="Sync tickets as GitHub/GitLab Issues")
    sync_wiki = models.BooleanField(default=False, help_text="Sync wiki pages")

    # Branch mapping
    fossil_branch = models.CharField(max_length=100, default="trunk", help_text="Fossil branch to sync")
    git_branch = models.CharField(max_length=100, default="main", help_text="Git branch to push to")

    # Status tracking
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=20, blank=True, default="")
    last_sync_message = models.TextField(blank=True, default="")
    total_syncs = models.PositiveIntegerField(default=0)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.repository.filename} → {self.git_remote_url}"


class SSHKey(Tracking):
    """SSH key pair for Git authentication."""

    name = models.CharField(max_length=200)
    public_key = models.TextField()
    private_key_path = models.CharField(max_length=500, help_text="Path to private key file on disk")
    fingerprint = models.CharField(max_length=100, blank=True, default="")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class SyncLog(models.Model):
    """Log entry for a sync operation."""

    mirror = models.ForeignKey(GitMirror, on_delete=models.CASCADE, related_name="logs")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default="running")  # running, success, failed
    artifacts_synced = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True, default="")
    triggered_by = models.CharField(max_length=20, default="manual")  # manual, schedule, on_change

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.mirror} @ {self.started_at:%Y-%m-%d %H:%M}"

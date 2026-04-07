"""Agent workspace model for isolated parallel development.

Each workspace corresponds to a Fossil branch and a temporary checkout directory
on disk. Agents can create, commit to, merge, and abandon workspaces independently.
"""

from django.db import models

from core.models import ActiveManager, Tracking


class AgentWorkspace(Tracking):
    """Isolated workspace for an agent working on a repository."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        MERGED = "merged", "Merged"
        ABANDONED = "abandoned", "Abandoned"

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="workspaces")
    name = models.CharField(max_length=200, help_text="Workspace name (e.g., agent-fix-bug-123)")
    branch = models.CharField(max_length=200, help_text="Fossil branch name for this workspace")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    agent_id = models.CharField(max_length=200, blank=True, default="", help_text="Agent identifier")
    description = models.CharField(max_length=500, blank=True, default="")
    checkout_path = models.CharField(max_length=500, blank=True, default="", help_text="Path to workspace checkout directory")

    # Work tracking
    files_changed = models.IntegerField(default=0)
    commits_made = models.IntegerField(default=0)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("repository", "name")]

    def __str__(self):
        return f"{self.name} ({self.status})"

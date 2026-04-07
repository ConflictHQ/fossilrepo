"""Branch protection rules for Fossil repositories.

Advisory for now -- the model and UI are ready, but enforcement via push hooks
is not yet implemented.
"""

from django.db import models

from core.models import ActiveManager, Tracking


class BranchProtection(Tracking):
    """Branch protection rules for a repository."""

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="branch_protections")
    branch_pattern = models.CharField(max_length=200, help_text="Branch name or glob pattern (e.g., 'trunk', 'release-*')")
    require_status_checks = models.BooleanField(default=False)
    required_contexts = models.TextField(blank=True, default="", help_text="Required CI contexts, one per line")
    restrict_push = models.BooleanField(default=True, help_text="Only admins can push")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["branch_pattern"]
        unique_together = [("repository", "branch_pattern")]

    def get_required_contexts_list(self):
        """Return required_contexts as a list, filtering blanks."""
        return [c.strip() for c in self.required_contexts.splitlines() if c.strip()]

    def __str__(self):
        return f"{self.branch_pattern} ({self.repository})"

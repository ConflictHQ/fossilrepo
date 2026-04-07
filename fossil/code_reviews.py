"""Code review models for agent-submitted review requests.

Agents working in workspaces submit diffs/patches for human review.
Reviews track the diff, comments, and approval workflow.
"""

from django.db import models

from core.models import ActiveManager, Tracking


class CodeReview(Tracking):
    """Agent-submitted code review request with diff and approval workflow."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        CHANGES_REQUESTED = "changes_requested", "Changes Requested"
        MERGED = "merged", "Merged"

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="code_reviews")
    workspace = models.ForeignKey("fossil.AgentWorkspace", null=True, blank=True, on_delete=models.SET_NULL, related_name="reviews")
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default="")
    diff = models.TextField(help_text="Unified diff of proposed changes")
    files_changed = models.JSONField(default=list)
    agent_id = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    ticket_uuid = models.CharField(max_length=64, blank=True, default="", help_text="Related ticket UUID if any")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Review: {self.title} ({self.status})"


class ReviewComment(Tracking):
    """Inline or general comment on a code review."""

    review = models.ForeignKey(CodeReview, on_delete=models.CASCADE, related_name="comments")
    body = models.TextField()
    file_path = models.CharField(max_length=500, blank=True, default="")
    line_number = models.IntegerField(null=True, blank=True)
    author = models.CharField(max_length=200, help_text="Agent ID or username")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        prefix = f"{self.file_path}:{self.line_number}" if self.file_path else "general"
        return f"Comment on {self.review_id} ({prefix})"

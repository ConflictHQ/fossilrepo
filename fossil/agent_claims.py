"""Agent ticket claim tracking for exclusive work assignment.

When multiple agents are working on a repository, they need a way to atomically
claim tickets so two agents don't work on the same issue simultaneously.
Claims are Django-side since Fossil tickets live in SQLite.
"""

from django.db import models

from core.models import ActiveManager, Tracking


class TicketClaim(Tracking):
    """Tracks which agent has claimed a Fossil ticket for exclusive work."""

    class Status(models.TextChoices):
        CLAIMED = "claimed", "Claimed"
        SUBMITTED = "submitted", "Submitted"
        MERGED = "merged", "Merged"
        RELEASED = "released", "Released"

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="ticket_claims")
    ticket_uuid = models.CharField(max_length=64)
    agent_id = models.CharField(max_length=200)
    workspace = models.ForeignKey("fossil.AgentWorkspace", null=True, blank=True, on_delete=models.SET_NULL, related_name="claims")
    claimed_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CLAIMED)
    summary = models.TextField(blank=True, default="", help_text="Work summary when submitted")
    files_changed = models.JSONField(default=list, blank=True, help_text="List of files changed")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        # Uniqueness for active claims is enforced at the application level
        # using select_for_update in the claim endpoint. We cannot use
        # unique_together because soft-deleted rows would violate the
        # constraint when the ticket is reclaimed.
        ordering = ["-claimed_at"]

    def __str__(self):
        return f"{self.ticket_uuid[:12]} claimed by {self.agent_id}"

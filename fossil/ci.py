"""CI status checks for Fossil checkins.

External CI systems (GitHub Actions, Jenkins, etc.) POST status results
for specific checkins. Results are displayed as badges on the checkin detail view.
"""

from django.db import models

from core.models import ActiveManager, Tracking


class StatusCheck(Tracking):
    """CI status check result for a specific checkin."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        ERROR = "error", "Error"

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="status_checks")
    checkin_uuid = models.CharField(max_length=64, db_index=True)
    context = models.CharField(max_length=200, help_text="CI context name (e.g., 'ci/tests', 'ci/lint')")
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    description = models.CharField(max_length=500, blank=True, default="")
    target_url = models.URLField(blank=True, default="", help_text="Link to CI build details")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("repository", "checkin_uuid", "context")]

    def __str__(self):
        return f"{self.context}: {self.state} @ {self.checkin_uuid[:10]}"

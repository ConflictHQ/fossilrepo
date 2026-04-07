"""Custom field definitions for project tickets.

Fossil's ticket system is schema-flexible -- the ``ticket`` table can
have arbitrary columns.  This model lets admins define extra fields
per repository so the Django UI can render them in create/edit forms
and pass values through to the Fossil CLI.
"""

from django.db import models

from core.models import ActiveManager, Tracking


class TicketFieldDefinition(Tracking):
    """Custom field definition for project tickets."""

    class FieldType(models.TextChoices):
        TEXT = "text", "Text"
        TEXTAREA = "textarea", "Multi-line Text"
        SELECT = "select", "Select (dropdown)"
        CHECKBOX = "checkbox", "Checkbox"
        DATE = "date", "Date"
        URL = "url", "URL"

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="ticket_fields")
    name = models.CharField(max_length=100, help_text="Field name (used in Fossil ticket system)")
    label = models.CharField(max_length=200, help_text="Display label")
    field_type = models.CharField(max_length=20, choices=FieldType.choices, default=FieldType.TEXT)
    choices = models.TextField(blank=True, default="", help_text="Options for select fields, one per line")
    is_required = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["sort_order", "name"]
        unique_together = [("repository", "name")]

    def __str__(self):
        return f"{self.label} ({self.name})"

    @property
    def choices_list(self):
        """Return choices as a list, filtering out blank lines."""
        if not self.choices:
            return []
        return [c.strip() for c in self.choices.splitlines() if c.strip()]

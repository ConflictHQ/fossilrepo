"""Outbound webhooks for project events.

Webhooks fire on Fossil events (checkin, ticket, wiki, release) and deliver
JSON payloads to configured URLs with HMAC signature verification.
"""

from django.db import models

from core.fields import EncryptedTextField
from core.models import ActiveManager, Tracking


class Webhook(Tracking):
    """Outbound webhook for project events."""

    class EventType(models.TextChoices):
        CHECKIN = "checkin", "New Checkin"
        TICKET = "ticket", "Ticket Change"
        WIKI = "wiki", "Wiki Edit"
        RELEASE = "release", "New Release"
        ALL = "all", "All Events"

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="webhooks")
    url = models.URLField(max_length=500)
    secret = EncryptedTextField(blank=True, default="")
    events = models.CharField(max_length=100, default="all")  # comma-separated event types
    is_active = models.BooleanField(default=True)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.url} ({self.events})"


class WebhookDelivery(models.Model):
    """Log of webhook delivery attempts."""

    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name="deliveries")
    event_type = models.CharField(max_length=20)
    payload = models.JSONField()
    response_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True, default="")
    success = models.BooleanField(default=False)
    delivered_at = models.DateTimeField(auto_now_add=True)
    duration_ms = models.IntegerField(default=0)
    attempt = models.IntegerField(default=1)

    class Meta:
        ordering = ["-delivered_at"]

    def __str__(self):
        return f"{self.webhook.url} @ {self.delivered_at}"

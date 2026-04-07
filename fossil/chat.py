"""Per-project chat backed by Django models.

Simple real-time-ish chat using HTMX polling. Messages stored in PostgreSQL.
"""

from django.contrib.auth.models import User
from django.db import models


class ChatMessage(models.Model):
    """A chat message in a project chatroom."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="chat_messages")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_messages")
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user.username}: {self.message[:50]}"

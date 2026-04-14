"""Real-time chat for Fossil repositories."""

from django.contrib.auth.models import User
from django.db import models


class ChatMessage(models.Model):
    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="chat_messages")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    username = models.CharField(max_length=150)  # denormalized display name
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.username}: {self.body[:50]}"

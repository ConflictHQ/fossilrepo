"""Django-backed forum posts for projects.

Supplements Fossil's native forum. Fossil's forum is tightly coupled to its
HTTP server and doesn't expose a CLI for creating posts, so we store
Django-side forum posts and display them alongside Fossil-native posts.
"""

from django.db import models

from core.models import ActiveManager, Tracking


class ForumPost(Tracking):
    """Django-backed forum post for projects. Supplements Fossil's native forum."""

    repository = models.ForeignKey("fossil.FossilRepository", on_delete=models.CASCADE, related_name="forum_posts")
    title = models.CharField(max_length=500, blank=True, default="")  # empty for replies
    body = models.TextField()
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies")
    # Thread root -- self-referencing for threading
    thread_root = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="thread_posts")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.title or f"Reply by {self.created_by}"

    @property
    def is_reply(self):
        return self.parent is not None

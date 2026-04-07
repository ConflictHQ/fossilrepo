"""Notification system for Fossilrepo.

Simple SMTP-based notifications for self-hosted deployments.
Users watch projects and get emails on checkins, tickets, wiki, forum changes.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import models

from core.models import ActiveManager, Tracking

logger = logging.getLogger(__name__)


class ProjectWatch(Tracking):
    """User's subscription to project notifications."""

    class EventType(models.TextChoices):
        ALL = "all", "All Events"
        CHECKINS = "checkins", "Checkins Only"
        TICKETS = "tickets", "Tickets Only"
        WIKI = "wiki", "Wiki Only"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="project_watches")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="watchers")
    event_filter = models.CharField(max_length=20, choices=EventType.choices, default=EventType.ALL)
    email_enabled = models.BooleanField(default=True)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ("user", "project")

    def __str__(self):
        return f"{self.user.username} watching {self.project.name}"


class Notification(models.Model):
    """Individual notification entry."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="notifications")
    event_type = models.CharField(max_length=20)  # checkin, ticket, wiki, forum
    title = models.CharField(max_length=300)
    body = models.TextField(blank=True, default="")
    url = models.CharField(max_length=500, blank=True, default="")
    read = models.BooleanField(default=False)
    emailed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} → {self.user.username}"


class NotificationPreference(models.Model):
    """Per-user notification delivery preferences."""

    class DeliveryMode(models.TextChoices):
        IMMEDIATE = "immediate", "Immediate (per event)"
        DAILY = "daily", "Daily Digest"
        WEEKLY = "weekly", "Weekly Digest"
        OFF = "off", "Off"

    user = models.OneToOneField("auth.User", on_delete=models.CASCADE, related_name="notification_prefs")
    delivery_mode = models.CharField(max_length=20, choices=DeliveryMode.choices, default=DeliveryMode.IMMEDIATE)

    # Event type toggles
    notify_checkins = models.BooleanField(default=True)
    notify_tickets = models.BooleanField(default=True)
    notify_wiki = models.BooleanField(default=True)
    notify_releases = models.BooleanField(default=True)
    notify_forum = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Notification Preference"

    def __str__(self):
        return f"{self.user.username}: {self.delivery_mode}"


def notify_project_event(project, event_type: str, title: str, body: str = "", url: str = "", exclude_user=None):
    """Create notifications for all watchers of a project.

    Args:
        project: Project instance
        event_type: "checkin", "ticket", "wiki", "forum"
        title: Short description
        body: Detail text
        url: Relative URL to the event
        exclude_user: Don't notify this user (typically the actor)
    """
    from django.template.loader import render_to_string

    watches = ProjectWatch.objects.filter(
        project=project,
        deleted_at__isnull=True,
        email_enabled=True,
    )

    for watch in watches:
        if exclude_user and watch.user == exclude_user:
            continue

        # Check event filter
        if watch.event_filter != "all" and watch.event_filter != event_type + "s":
            continue

        # Skip non-immediate users -- they get digests instead
        prefs = NotificationPreference.objects.filter(user=watch.user).first()
        if prefs and prefs.delivery_mode != "immediate":
            # Still create the notification record for the digest
            Notification.objects.create(
                user=watch.user,
                project=project,
                event_type=event_type,
                title=title,
                body=body,
                url=url,
            )
            continue

        notification = Notification.objects.create(
            user=watch.user,
            project=project,
            event_type=event_type,
            title=title,
            body=body,
            url=url,
        )

        # Send email with HTML template
        if watch.email_enabled and watch.user.email:
            try:
                subject = f"[{project.name}] {event_type}: {title[:80]}"
                text_body = f"{title}\n\n{body}\n\nView: {url}" if url else f"{title}\n\n{body}"
                html_body = render_to_string("email/notification.html", {
                    "event_type": event_type,
                    "project_name": project.name,
                    "message": body or title,
                    "action_url": url,
                    "project_url": f"/projects/{project.slug}/",
                    "unsubscribe_url": f"/projects/{project.slug}/fossil/watch/",
                    "preferences_url": "/auth/notifications/",
                })
                send_mail(
                    subject=subject,
                    message=text_body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[watch.user.email],
                    html_message=html_body,
                    fail_silently=True,
                )
                notification.emailed = True
                notification.save(update_fields=["emailed"])
            except Exception:
                logger.exception("Failed to send notification email to %s", watch.user.email)

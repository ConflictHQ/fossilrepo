"""Tests for HTML email notification templates and updated sending logic.

Verifies that notify_project_event and send_digest produce HTML emails
using the templates, include plain text fallbacks, and respect delivery
mode preferences.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string

from fossil.notifications import Notification, NotificationPreference, ProjectWatch, notify_project_event

# --- Template rendering tests ---


@pytest.mark.django_db
class TestNotificationTemplateRendering:
    def test_notification_template_renders(self):
        html = render_to_string("email/notification.html", {
            "event_type": "checkin",
            "project_name": "My Project",
            "message": "Added new feature",
            "action_url": "/projects/my-project/fossil/checkin/abc123/",
            "project_url": "/projects/my-project/",
            "unsubscribe_url": "/projects/my-project/fossil/watch/",
            "preferences_url": "/auth/notifications/",
        })
        assert "fossilrepo" in html
        assert "My Project" in html
        assert "Added new feature" in html
        assert "checkin" in html
        assert "View Details" in html
        assert "/projects/my-project/fossil/checkin/abc123/" in html
        assert "Unsubscribe" in html

    def test_notification_template_without_action_url(self):
        html = render_to_string("email/notification.html", {
            "event_type": "ticket",
            "project_name": "My Project",
            "message": "New ticket filed",
            "action_url": "",
            "project_url": "/projects/my-project/",
            "unsubscribe_url": "/projects/my-project/fossil/watch/",
            "preferences_url": "/auth/notifications/",
        })
        assert "View Details" not in html
        assert "New ticket filed" in html

    def test_notification_template_event_types(self):
        for event_type in ["checkin", "ticket", "wiki", "release", "forum"]:
            html = render_to_string("email/notification.html", {
                "event_type": event_type,
                "project_name": "Test",
                "message": "Test message",
                "action_url": "",
                "project_url": "/projects/test/",
                "unsubscribe_url": "/projects/test/fossil/watch/",
                "preferences_url": "/auth/notifications/",
            })
            assert event_type in html

    def test_digest_template_renders(self):
        class MockNotif:
            def __init__(self, event_type, title, project_name):
                self.event_type = event_type
                self.title = title

                class MockProject:
                    name = project_name

                self.project = MockProject()

        notifications = [
            MockNotif("checkin", "Added login page", "Frontend"),
            MockNotif("ticket", "Bug: 404 on settings", "Backend"),
            MockNotif("wiki", "Updated README", "Docs"),
        ]
        html = render_to_string("email/digest.html", {
            "digest_type": "daily",
            "count": 3,
            "notifications": notifications,
            "overflow_count": 0,
            "dashboard_url": "/",
            "preferences_url": "/auth/notifications/",
        })
        assert "Daily Digest" in html
        assert "3 update" in html
        assert "Frontend" in html
        assert "Backend" in html
        assert "Docs" in html
        assert "Added login page" in html
        assert "View All Notifications" in html

    def test_digest_template_overflow(self):
        html = render_to_string("email/digest.html", {
            "digest_type": "weekly",
            "count": 75,
            "notifications": [],
            "overflow_count": 25,
            "dashboard_url": "/",
            "preferences_url": "/auth/notifications/",
        })
        assert "Weekly Digest" in html
        assert "75 update" in html
        assert "25 more" in html


# --- notify_project_event HTML email tests ---


@pytest.mark.django_db
class TestNotifyProjectEventHTML:
    @pytest.fixture
    def watcher_user(self, db, admin_user, sample_project):
        user = User.objects.create_user(username="watcher_email", email="watcher@test.com", password="testpass123")
        ProjectWatch.objects.create(user=user, project=sample_project, email_enabled=True, created_by=admin_user)
        return user

    @pytest.fixture
    def daily_watcher(self, db, admin_user, sample_project):
        user = User.objects.create_user(username="daily_watcher", email="daily@test.com", password="testpass123")
        ProjectWatch.objects.create(user=user, project=sample_project, email_enabled=True, created_by=admin_user)
        NotificationPreference.objects.create(user=user, delivery_mode="daily")
        return user

    def test_immediate_sends_html_email(self, watcher_user, sample_project):
        with patch("fossil.notifications.send_mail") as mock_send:
            notify_project_event(
                project=sample_project,
                event_type="checkin",
                title="New commit",
                body="Added login feature",
                url="/projects/frontend-app/fossil/checkin/abc/",
            )

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        assert "html_message" in call_kwargs
        assert "fossilrepo" in call_kwargs["html_message"]
        assert "checkin" in call_kwargs["html_message"]
        assert "Added login feature" in call_kwargs["html_message"]
        # Plain text fallback is also present
        assert call_kwargs["message"] != ""

    def test_immediate_subject_format(self, watcher_user, sample_project):
        with patch("fossil.notifications.send_mail") as mock_send:
            notify_project_event(
                project=sample_project,
                event_type="ticket",
                title="Bug report: login broken",
                body="Users can't log in",
            )

        call_kwargs = mock_send.call_args.kwargs
        assert "[Frontend App]" in call_kwargs["subject"]
        assert "ticket:" in call_kwargs["subject"]

    def test_daily_user_not_emailed_immediately(self, daily_watcher, sample_project):
        with patch("fossil.notifications.send_mail") as mock_send:
            notify_project_event(
                project=sample_project,
                event_type="checkin",
                title="New commit",
                body="Some change",
            )

        mock_send.assert_not_called()
        # But notification record is still created for digest
        assert Notification.objects.filter(user=daily_watcher).count() == 1

    def test_notification_created_for_immediate_user(self, watcher_user, sample_project):
        with patch("fossil.notifications.send_mail"):
            notify_project_event(
                project=sample_project,
                event_type="wiki",
                title="Wiki updated",
                body="New page",
            )

        notif = Notification.objects.get(user=watcher_user)
        assert notif.event_type == "wiki"
        assert notif.title == "Wiki updated"
        assert notif.emailed is True


# --- send_digest HTML email tests ---


@pytest.mark.django_db
class TestSendDigestHTML:
    @pytest.fixture
    def daily_user(self, db):
        user = User.objects.create_user(username="daily_html", email="daily_html@test.com", password="testpass123")
        NotificationPreference.objects.create(user=user, delivery_mode="daily")
        return user

    def test_digest_sends_html_email(self, daily_user, sample_project):
        for i in range(3):
            Notification.objects.create(
                user=daily_user,
                project=sample_project,
                event_type="checkin",
                title=f"Commit #{i}",
            )

        from fossil.tasks import send_digest

        with patch("fossil.tasks.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "daily"})

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        assert "html_message" in call_kwargs
        assert "Daily Digest" in call_kwargs["html_message"]
        assert "3 update" in call_kwargs["html_message"]
        assert "fossilrepo" in call_kwargs["html_message"]
        # Plain text fallback
        assert "3 new notifications" in call_kwargs["message"]

    def test_digest_html_includes_project_names(self, daily_user, sample_project):
        Notification.objects.create(
            user=daily_user,
            project=sample_project,
            event_type="ticket",
            title="Bug filed",
        )

        from fossil.tasks import send_digest

        with patch("fossil.tasks.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "daily"})

        call_kwargs = mock_send.call_args.kwargs
        assert sample_project.name in call_kwargs["html_message"]

    def test_digest_html_overflow_message(self, daily_user, sample_project):
        for i in range(55):
            Notification.objects.create(
                user=daily_user,
                project=sample_project,
                event_type="checkin",
                title=f"Commit #{i}",
            )

        from fossil.tasks import send_digest

        with patch("fossil.tasks.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "daily"})

        call_kwargs = mock_send.call_args.kwargs
        assert "5 more" in call_kwargs["html_message"]

    def test_weekly_digest_html(self, db):
        user = User.objects.create_user(username="weekly_html", email="weekly_html@test.com", password="testpass123")
        NotificationPreference.objects.create(user=user, delivery_mode="weekly")

        from organization.models import Organization
        from projects.models import Project

        org = Organization.objects.create(name="Test Org Digest")
        project = Project.objects.create(name="Digest Project", organization=org, visibility="private")

        Notification.objects.create(user=user, project=project, event_type="wiki", title="Wiki edit")

        from fossil.tasks import send_digest

        with patch("fossil.tasks.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "weekly"})

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        assert "Weekly Digest" in call_kwargs["html_message"]

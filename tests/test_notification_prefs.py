from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from fossil.notifications import Notification, NotificationPreference

# --- NotificationPreference Model Tests ---


@pytest.mark.django_db
class TestNotificationPreferenceModel:
    def test_create_preference(self, admin_user):
        pref = NotificationPreference.objects.create(user=admin_user)
        assert pref.pk is not None
        assert pref.delivery_mode == "immediate"
        assert pref.notify_checkins is True
        assert pref.notify_tickets is True
        assert pref.notify_wiki is True
        assert pref.notify_releases is True
        assert pref.notify_forum is False

    def test_str_repr(self, admin_user):
        pref = NotificationPreference.objects.create(user=admin_user, delivery_mode="daily")
        assert str(pref) == "admin: daily"

    def test_one_to_one_constraint(self, admin_user):
        NotificationPreference.objects.create(user=admin_user)
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            NotificationPreference.objects.create(user=admin_user)

    def test_delivery_mode_choices(self, admin_user):
        for mode in ["immediate", "daily", "weekly", "off"]:
            pref, _ = NotificationPreference.objects.update_or_create(user=admin_user, defaults={"delivery_mode": mode})
            pref.refresh_from_db()
            assert pref.delivery_mode == mode


# --- Notification Preferences View Tests ---


@pytest.mark.django_db
class TestNotificationPreferencesView:
    def test_get_creates_default_prefs(self, admin_client, admin_user):
        assert not NotificationPreference.objects.filter(user=admin_user).exists()
        response = admin_client.get("/auth/notifications/")
        assert response.status_code == 200
        assert "Notification Preferences" in response.content.decode()
        assert NotificationPreference.objects.filter(user=admin_user).exists()

    def test_get_renders_existing_prefs(self, admin_client, admin_user):
        NotificationPreference.objects.create(user=admin_user, delivery_mode="daily", notify_forum=True)
        response = admin_client.get("/auth/notifications/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Notification Preferences" in content

    def test_post_updates_delivery_mode(self, admin_client, admin_user):
        NotificationPreference.objects.create(user=admin_user)
        response = admin_client.post(
            "/auth/notifications/",
            {
                "delivery_mode": "daily",
                "notify_checkins": "on",
                "notify_tickets": "on",
            },
        )
        assert response.status_code == 302
        pref = NotificationPreference.objects.get(user=admin_user)
        assert pref.delivery_mode == "daily"
        assert pref.notify_checkins is True
        assert pref.notify_tickets is True
        assert pref.notify_wiki is False
        assert pref.notify_releases is False
        assert pref.notify_forum is False

    def test_post_updates_event_toggles(self, admin_client, admin_user):
        NotificationPreference.objects.create(user=admin_user)
        response = admin_client.post(
            "/auth/notifications/",
            {
                "delivery_mode": "weekly",
                "notify_checkins": "on",
                "notify_tickets": "on",
                "notify_wiki": "on",
                "notify_releases": "on",
                "notify_forum": "on",
            },
        )
        assert response.status_code == 302
        pref = NotificationPreference.objects.get(user=admin_user)
        assert pref.delivery_mode == "weekly"
        assert pref.notify_checkins is True
        assert pref.notify_tickets is True
        assert pref.notify_wiki is True
        assert pref.notify_releases is True
        assert pref.notify_forum is True

    def test_post_turn_off(self, admin_client, admin_user):
        NotificationPreference.objects.create(user=admin_user, delivery_mode="daily")
        response = admin_client.post(
            "/auth/notifications/",
            {
                "delivery_mode": "off",
            },
        )
        assert response.status_code == 302
        pref = NotificationPreference.objects.get(user=admin_user)
        assert pref.delivery_mode == "off"
        # All unchecked checkboxes default to False
        assert pref.notify_checkins is False
        assert pref.notify_tickets is False

    def test_denied_for_anon(self, client):
        response = client.get("/auth/notifications/")
        assert response.status_code == 302  # redirect to login


# --- Digest Task Tests ---


@pytest.mark.django_db
class TestSendDigestTask:
    @pytest.fixture
    def daily_user(self, db):
        user = User.objects.create_user(username="dailyuser", email="daily@test.com", password="testpass123")
        NotificationPreference.objects.create(user=user, delivery_mode="daily")
        return user

    @pytest.fixture
    def weekly_user(self, db):
        user = User.objects.create_user(username="weeklyuser", email="weekly@test.com", password="testpass123")
        NotificationPreference.objects.create(user=user, delivery_mode="weekly")
        return user

    @pytest.fixture
    def immediate_user(self, db):
        user = User.objects.create_user(username="immediateuser", email="immediate@test.com", password="testpass123")
        NotificationPreference.objects.create(user=user, delivery_mode="immediate")
        return user

    def test_daily_digest_sends_email(self, daily_user, sample_project):
        # Create unread notifications
        for i in range(3):
            Notification.objects.create(
                user=daily_user,
                project=sample_project,
                event_type="checkin",
                title=f"Commit #{i}",
            )

        from fossil.tasks import send_digest

        with patch("django.core.mail.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "daily"})

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        assert "Daily" in call_kwargs[1]["subject"] or "Daily" in call_kwargs[0][0]
        assert daily_user.email in (call_kwargs[1].get("recipient_list") or call_kwargs[0][3])

        # Notifications marked as read
        assert Notification.objects.filter(user=daily_user, read=False).count() == 0

    def test_weekly_digest_sends_email(self, weekly_user, sample_project):
        Notification.objects.create(
            user=weekly_user,
            project=sample_project,
            event_type="ticket",
            title="New ticket",
        )

        from fossil.tasks import send_digest

        with patch("django.core.mail.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "weekly"})

        mock_send.assert_called_once()

    def test_no_email_for_immediate_users(self, immediate_user, sample_project):
        Notification.objects.create(
            user=immediate_user,
            project=sample_project,
            event_type="checkin",
            title="Commit",
        )

        from fossil.tasks import send_digest

        with patch("django.core.mail.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "daily"})

        mock_send.assert_not_called()

    def test_no_email_when_no_unread(self, daily_user, sample_project):
        # Create read notifications
        Notification.objects.create(
            user=daily_user,
            project=sample_project,
            event_type="checkin",
            title="Old commit",
            read=True,
        )

        from fossil.tasks import send_digest

        with patch("django.core.mail.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "daily"})

        mock_send.assert_not_called()

    def test_digest_limits_to_50_notifications(self, daily_user, sample_project):
        for i in range(55):
            Notification.objects.create(
                user=daily_user,
                project=sample_project,
                event_type="checkin",
                title=f"Commit #{i}",
            )

        from fossil.tasks import send_digest

        with patch("django.core.mail.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "daily"})

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        message = call_args[1].get("message") or call_args[0][1]
        assert "55 new notifications" in message
        assert "and 5 more" in message

        # All 55 marked as read
        assert Notification.objects.filter(user=daily_user, read=False).count() == 0

    def test_digest_no_users_with_mode(self):
        """When no users have the requested delivery mode, task completes without error."""
        from fossil.tasks import send_digest

        with patch("django.core.mail.send_mail") as mock_send:
            send_digest.apply(kwargs={"mode": "daily"})

        mock_send.assert_not_called()

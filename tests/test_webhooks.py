import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.models import FossilRepository
from fossil.webhooks import Webhook, WebhookDelivery
from organization.models import Team
from projects.models import ProjectTeam


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def webhook(fossil_repo_obj, admin_user):
    return Webhook.objects.create(
        repository=fossil_repo_obj,
        url="https://example.com/webhook",
        secret="test-secret",
        events="all",
        is_active=True,
        created_by=admin_user,
    )


@pytest.fixture
def inactive_webhook(fossil_repo_obj, admin_user):
    return Webhook.objects.create(
        repository=fossil_repo_obj,
        url="https://example.com/inactive",
        secret="",
        events="checkin",
        is_active=False,
        created_by=admin_user,
    )


@pytest.fixture
def delivery(webhook):
    return WebhookDelivery.objects.create(
        webhook=webhook,
        event_type="checkin",
        payload={"hash": "abc123", "user": "dev"},
        response_status=200,
        response_body="OK",
        success=True,
        duration_ms=150,
        attempt=1,
    )


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """User with write access but not admin."""
    writer = User.objects.create_user(username="writer", password="testpass123")
    team = Team.objects.create(name="Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="writer", password="testpass123")
    return client


# --- Webhook Model Tests ---


@pytest.mark.django_db
class TestWebhookModel:
    def test_create_webhook(self, webhook):
        assert webhook.pk is not None
        assert str(webhook) == "https://example.com/webhook (all)"

    def test_soft_delete(self, webhook, admin_user):
        webhook.soft_delete(user=admin_user)
        assert webhook.is_deleted
        assert Webhook.objects.filter(pk=webhook.pk).count() == 0
        assert Webhook.all_objects.filter(pk=webhook.pk).count() == 1

    def test_secret_encrypted_at_rest(self, webhook):
        """EncryptedTextField encrypts the value in the DB."""
        # Read raw value from DB bypassing the field's from_db_value
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT secret FROM fossil_webhook WHERE id = %s", [webhook.pk])
            raw = cursor.fetchone()[0]
        # Raw DB value should NOT be the plaintext
        assert raw != "test-secret"
        # But accessing via the model decrypts it
        webhook.refresh_from_db()
        assert webhook.secret == "test-secret"

    def test_ordering(self, fossil_repo_obj, admin_user):
        w1 = Webhook.objects.create(repository=fossil_repo_obj, url="https://a.com/hook", events="all", created_by=admin_user)
        w2 = Webhook.objects.create(repository=fossil_repo_obj, url="https://b.com/hook", events="all", created_by=admin_user)
        hooks = list(Webhook.objects.filter(repository=fossil_repo_obj))
        # Ordered by -created_at, so newest first
        assert hooks[0] == w2
        assert hooks[1] == w1


@pytest.mark.django_db
class TestWebhookDeliveryModel:
    def test_create_delivery(self, delivery):
        assert delivery.pk is not None
        assert delivery.success is True
        assert delivery.response_status == 200
        assert "abc123" in json.dumps(delivery.payload)

    def test_delivery_str(self, delivery):
        assert "example.com/webhook" in str(delivery)

    def test_ordering(self, webhook):
        d1 = WebhookDelivery.objects.create(webhook=webhook, event_type="checkin", payload={}, success=True)
        d2 = WebhookDelivery.objects.create(webhook=webhook, event_type="ticket", payload={}, success=False)
        deliveries = list(WebhookDelivery.objects.filter(webhook=webhook))
        # Ordered by -delivered_at, so newest first
        assert deliveries[0] == d2
        assert deliveries[1] == d1


# --- Webhook List View Tests ---


@pytest.mark.django_db
class TestWebhookListView:
    def test_list_webhooks(self, admin_client, sample_project, webhook):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "example.com/webhook" in content
        assert "Active" in content

    def test_list_empty(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/")
        assert response.status_code == 200
        assert "No webhooks configured" in response.content.decode()

    def test_list_denied_for_writer(self, writer_client, sample_project, webhook):
        """Webhook management requires admin, not just write."""
        response = writer_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/")
        assert response.status_code == 403

    def test_list_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/")
        assert response.status_code == 403

    def test_list_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/webhooks/")
        assert response.status_code == 302  # redirect to login


# --- Webhook Create View Tests ---


@pytest.mark.django_db
class TestWebhookCreateView:
    def test_get_form(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/create/")
        assert response.status_code == 200
        assert "Create Webhook" in response.content.decode()

    def test_create_webhook(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/webhooks/create/",
            {"url": "https://hooks.example.com/test", "secret": "s3cret", "events": ["checkin", "ticket"], "is_active": "on"},
        )
        assert response.status_code == 302
        hook = Webhook.objects.get(url="https://hooks.example.com/test")
        assert hook.secret == "s3cret"
        assert hook.events == "checkin,ticket"
        assert hook.is_active is True

    def test_create_webhook_all_events(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/webhooks/create/",
            {"url": "https://hooks.example.com/all", "is_active": "on"},
        )
        assert response.status_code == 302
        hook = Webhook.objects.get(url="https://hooks.example.com/all")
        assert hook.events == "all"

    def test_create_denied_for_writer(self, writer_client, sample_project):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/webhooks/create/",
            {"url": "https://evil.com/hook"},
        )
        assert response.status_code == 403


# --- Webhook Edit View Tests ---


@pytest.mark.django_db
class TestWebhookEditView:
    def test_get_edit_form(self, admin_client, sample_project, webhook):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "example.com/webhook" in content
        assert "Update Webhook" in content

    def test_edit_webhook(self, admin_client, sample_project, webhook):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/edit/",
            {"url": "https://new-url.example.com/hook", "events": ["wiki"], "is_active": "on"},
        )
        assert response.status_code == 302
        webhook.refresh_from_db()
        assert webhook.url == "https://new-url.example.com/hook"
        assert webhook.events == "wiki"

    def test_edit_preserves_secret_when_blank(self, admin_client, sample_project, webhook):
        """Editing without providing a new secret should keep the old one."""
        old_secret = webhook.secret
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/edit/",
            {"url": "https://example.com/webhook", "secret": "", "events": ["all"], "is_active": "on"},
        )
        assert response.status_code == 302
        webhook.refresh_from_db()
        assert webhook.secret == old_secret

    def test_edit_denied_for_writer(self, writer_client, sample_project, webhook):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/edit/",
            {"url": "https://evil.com/hook"},
        )
        assert response.status_code == 403

    def test_edit_nonexistent_webhook(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/99999/edit/")
        assert response.status_code == 404


# --- Webhook Delete View Tests ---


@pytest.mark.django_db
class TestWebhookDeleteView:
    def test_delete_webhook(self, admin_client, sample_project, webhook):
        response = admin_client.post(f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/delete/")
        assert response.status_code == 302
        webhook.refresh_from_db()
        assert webhook.is_deleted

    def test_delete_get_redirects(self, admin_client, sample_project, webhook):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/delete/")
        assert response.status_code == 302  # GET redirects to list

    def test_delete_denied_for_writer(self, writer_client, sample_project, webhook):
        response = writer_client.post(f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/delete/")
        assert response.status_code == 403


# --- Webhook Deliveries View Tests ---


@pytest.mark.django_db
class TestWebhookDeliveriesView:
    def test_view_deliveries(self, admin_client, sample_project, webhook, delivery):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/deliveries/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "checkin" in content
        assert "200" in content
        assert "150ms" in content

    def test_view_empty_deliveries(self, admin_client, sample_project, webhook):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/deliveries/")
        assert response.status_code == 200
        assert "No deliveries yet" in response.content.decode()

    def test_deliveries_denied_for_writer(self, writer_client, sample_project, webhook):
        response = writer_client.get(f"/projects/{sample_project.slug}/fossil/webhooks/{webhook.pk}/deliveries/")
        assert response.status_code == 403


# --- Webhook Dispatch Task Tests ---


@pytest.mark.django_db
class TestDispatchWebhookTask:
    def test_successful_delivery(self, webhook):
        """Test dispatch_webhook task with a successful response."""
        from fossil.tasks import dispatch_webhook

        mock_response = type("Response", (), {"status_code": 200, "text": "OK"})()

        with patch("requests.post", return_value=mock_response):
            dispatch_webhook.apply(args=[webhook.pk, "checkin", {"hash": "abc"}])

        delivery = WebhookDelivery.objects.get(webhook=webhook)
        assert delivery.success is True
        assert delivery.response_status == 200
        assert delivery.event_type == "checkin"

    def test_failed_delivery_logs(self, webhook):
        """Test that failed HTTP responses are logged and delivery is recorded."""
        from fossil.tasks import dispatch_webhook

        mock_response = type("Response", (), {"status_code": 500, "text": "Internal Server Error"})()

        with patch("requests.post", return_value=mock_response):
            dispatch_webhook.apply(args=[webhook.pk, "checkin", {"hash": "abc"}])

        # The task retries on non-2xx, so apply() catches the Retry internally
        delivery = WebhookDelivery.objects.filter(webhook=webhook).first()
        assert delivery is not None
        assert delivery.success is False
        assert delivery.response_status == 500

    def test_nonexistent_webhook_no_crash(self):
        """Task should handle missing webhook gracefully."""
        from fossil.tasks import dispatch_webhook

        # Should return without error (logs warning)
        dispatch_webhook.apply(args=[99999, "checkin", {"hash": "abc"}])
        # No deliveries created
        assert WebhookDelivery.objects.count() == 0

    def test_hmac_signature_set_with_secret(self, webhook):
        """When webhook has a secret, X-Fossilrepo-Signature header is set."""
        from fossil.tasks import dispatch_webhook

        mock_response = type("Response", (), {"status_code": 200, "text": "OK"})()
        captured_kwargs = {}

        def capture_post(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_response

        with patch("requests.post", side_effect=capture_post):
            dispatch_webhook.apply(args=[webhook.pk, "checkin", {"hash": "abc"}])

        headers = captured_kwargs.get("headers", {})
        assert "X-Fossilrepo-Signature" in headers
        assert headers["X-Fossilrepo-Signature"].startswith("sha256=")

    def test_no_signature_without_secret(self, fossil_repo_obj, admin_user):
        """When webhook has no secret, no signature header is sent."""
        from fossil.tasks import dispatch_webhook

        hook = Webhook.objects.create(
            repository=fossil_repo_obj,
            url="https://no-secret.example.com/hook",
            secret="",
            events="all",
            is_active=True,
            created_by=admin_user,
        )

        mock_response = type("Response", (), {"status_code": 200, "text": "OK"})()
        captured_kwargs = {}

        def capture_post(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_response

        with patch("requests.post", side_effect=capture_post):
            dispatch_webhook.apply(args=[hook.pk, "checkin", {"hash": "abc"}])

        headers = captured_kwargs.get("headers", {})
        assert "X-Fossilrepo-Signature" not in headers

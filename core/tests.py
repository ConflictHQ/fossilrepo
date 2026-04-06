import pytest
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .permissions import P


class TrackingModelTest(TestCase):
    """Test the Tracking abstract model via a concrete model that uses it."""

    def setUp(self):
        from items.models import Item

        self.user = User.objects.create_superuser(username="test", password="x")
        self.item = Item.objects.create(name="Test Widget", price="9.99", created_by=self.user)

    def test_version_increments_on_save(self):
        initial_version = self.item.version
        self.item.name = "Updated Widget"
        self.item.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.version, initial_version + 1)

    def test_soft_delete_sets_deleted_at(self):
        self.item.soft_delete(user=self.user)
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.deleted_at)
        self.assertEqual(self.item.deleted_by, self.user)
        self.assertTrue(self.item.is_deleted)

    def test_created_at_auto_set(self):
        self.assertIsNotNone(self.item.created_at)

    def test_updated_at_auto_set(self):
        self.assertIsNotNone(self.item.updated_at)


class BaseCoreModelTest(TestCase):
    """Test BaseCoreModel slug generation and UUID."""

    def setUp(self):
        from items.models import Item

        self.user = User.objects.create_superuser(username="test", password="x")
        self.item = Item.objects.create(name="My Item", price="19.99", created_by=self.user)

    def test_slug_auto_generated(self):
        self.assertEqual(self.item.slug, "my-item")

    def test_guid_is_uuid(self):
        import uuid

        self.assertIsInstance(self.item.guid, uuid.UUID)

    def test_slug_uniqueness(self):
        from items.models import Item

        p2 = Item.objects.create(name="My Item", price="29.99", created_by=self.user)
        self.assertNotEqual(self.item.slug, p2.slug)
        self.assertTrue(p2.slug.startswith("my-item"))

    def test_str_returns_name(self):
        self.assertEqual(str(self.item), "My Item")


class PermissionsTest(TestCase):
    """Test the P permission enum."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(username="super", password="x")
        self.regular = User.objects.create_user(username="regular", password="x")

    def test_superuser_passes_all_checks(self):
        self.assertTrue(P.ITEM_VIEW.check(self.superuser))
        self.assertTrue(P.ITEM_ADD.check(self.superuser))

    def test_regular_user_without_perm_denied(self):
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            P.ITEM_ADD.check(self.regular)

    def test_regular_user_without_perm_returns_false(self):
        self.assertFalse(P.ITEM_ADD.check(self.regular, raise_error=False))

    def test_unauthenticated_user_denied(self):
        from django.contrib.auth.models import AnonymousUser
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            P.ITEM_VIEW.check(AnonymousUser())


@pytest.mark.django_db
class TestDashboard:
    def test_dashboard_requires_login(self, client):
        response = client.get(reverse("dashboard"))
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_dashboard_renders_for_authenticated_user(self, admin_client):
        response = admin_client.get(reverse("dashboard"))
        assert response.status_code == 200
        assert b"Dashboard" in response.content


@pytest.mark.django_db
class TestHealthCheck:
    def test_health_returns_ok(self, client):
        response = client.get(reverse("health"))
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "fossilrepo-django-htmx"
        assert "version" in data
        assert "uptime" in data
        assert "timestamp" in data
        assert data["checks"]["database"] == "ok"
        assert data["links"]["status"] == "/status/"


@pytest.mark.django_db
class TestStatusPage:
    def test_status_page_accessible_unauthenticated(self, client):
        response = client.get(reverse("status"))
        assert response.status_code == 200
        assert b"Fossilrepo" in response.content
        assert b"Server-rendered Django + HTMX." in response.content

    def test_status_page_contains_links(self, client):
        response = client.get(reverse("status"))
        content = response.content.decode()
        assert "/dashboard/" in content
        assert "/admin/" in content
        assert "/health/" in content
        assert "/auth/login/" in content

    def test_status_page_contains_meta(self, client):
        response = client.get(reverse("status"))
        content = response.content.decode()
        assert "fossilrepo-django-htmx" in content
        assert "All systems operational" in content

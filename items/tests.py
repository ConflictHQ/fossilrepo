import pytest
from django.urls import reverse

from .models import Item


@pytest.fixture
def sample_item(db, admin_user):
    return Item.objects.create(name="Test Widget", price="29.99", sku="TST-001", created_by=admin_user)


@pytest.mark.django_db
class TestItemList:
    def test_list_requires_login(self, client):
        response = client.get(reverse("items:list"))
        assert response.status_code == 302

    def test_list_renders_for_superuser(self, admin_client, sample_item):
        response = admin_client.get(reverse("items:list"))
        assert response.status_code == 200
        assert b"Test Widget" in response.content

    def test_list_renders_for_viewer(self, viewer_client, sample_item):
        response = viewer_client.get(reverse("items:list"))
        assert response.status_code == 200
        assert b"Test Widget" in response.content

    def test_list_denied_for_user_without_perm(self, no_perm_client, sample_item):
        response = no_perm_client.get(reverse("items:list"))
        assert response.status_code == 403

    def test_list_htmx_returns_partial(self, admin_client, sample_item):
        response = admin_client.get(reverse("items:list"), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b"item-table" in response.content
        assert b"<!DOCTYPE" not in response.content  # partial, not full page

    def test_list_search_filters(self, admin_client, admin_user):
        Item.objects.create(name="Alpha", price="10.00", created_by=admin_user)
        Item.objects.create(name="Beta", price="20.00", created_by=admin_user)
        response = admin_client.get(reverse("items:list") + "?search=Alpha")
        assert b"Alpha" in response.content
        assert b"Beta" not in response.content


@pytest.mark.django_db
class TestItemCreate:
    def test_create_form_renders(self, admin_client):
        response = admin_client.get(reverse("items:create"))
        assert response.status_code == 200
        assert b"New Item" in response.content

    def test_create_saves_item(self, admin_client, admin_user):
        response = admin_client.post(
            reverse("items:create"),
            {"name": "New Gadget", "description": "A new gadget", "price": "49.99", "sku": "NGT-001", "is_active": True},
        )
        assert response.status_code == 302
        item = Item.objects.get(sku="NGT-001")
        assert item.name == "New Gadget"
        assert item.created_by == admin_user

    def test_create_denied_for_viewer(self, viewer_client):
        response = viewer_client.get(reverse("items:create"))
        assert response.status_code == 403

    def test_create_invalid_data_shows_errors(self, admin_client):
        response = admin_client.post(reverse("items:create"), {"name": "", "price": ""})
        assert response.status_code == 200  # re-renders form with errors


@pytest.mark.django_db
class TestItemDetail:
    def test_detail_renders(self, admin_client, sample_item):
        response = admin_client.get(reverse("items:detail", kwargs={"slug": sample_item.slug}))
        assert response.status_code == 200
        assert b"Test Widget" in response.content
        assert str(sample_item.guid).encode() in response.content

    def test_detail_404_for_deleted(self, admin_client, sample_item, admin_user):
        sample_item.soft_delete(user=admin_user)
        response = admin_client.get(reverse("items:detail", kwargs={"slug": sample_item.slug}))
        assert response.status_code == 404


@pytest.mark.django_db
class TestItemUpdate:
    def test_update_form_renders(self, admin_client, sample_item):
        response = admin_client.get(reverse("items:update", kwargs={"slug": sample_item.slug}))
        assert response.status_code == 200
        assert b"Edit Item" in response.content

    def test_update_saves_changes(self, admin_client, sample_item):
        response = admin_client.post(
            reverse("items:update", kwargs={"slug": sample_item.slug}),
            {"name": "Updated Widget", "description": "Updated", "price": "39.99", "sku": "TST-001", "is_active": True},
        )
        assert response.status_code == 302
        sample_item.refresh_from_db()
        assert sample_item.name == "Updated Widget"
        from decimal import Decimal

        assert sample_item.price == Decimal("39.99")

    def test_update_denied_for_viewer(self, viewer_client, sample_item):
        response = viewer_client.get(reverse("items:update", kwargs={"slug": sample_item.slug}))
        assert response.status_code == 403


@pytest.mark.django_db
class TestItemDelete:
    def test_delete_confirm_renders(self, admin_client, sample_item):
        response = admin_client.get(reverse("items:delete", kwargs={"slug": sample_item.slug}))
        assert response.status_code == 200
        assert b"Delete Item" in response.content

    def test_delete_soft_deletes(self, admin_client, sample_item):
        response = admin_client.post(reverse("items:delete", kwargs={"slug": sample_item.slug}))
        assert response.status_code == 302
        sample_item.refresh_from_db()
        assert sample_item.is_deleted

    def test_delete_htmx_returns_redirect_header(self, admin_client, sample_item):
        response = admin_client.post(
            reverse("items:delete", kwargs={"slug": sample_item.slug}),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert response.headers.get("HX-Redirect") == "/items/"

    def test_delete_denied_for_viewer(self, viewer_client, sample_item):
        response = viewer_client.post(reverse("items:delete", kwargs={"slug": sample_item.slug}))
        assert response.status_code == 403

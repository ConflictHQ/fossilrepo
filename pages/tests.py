import pytest

from .models import Page


@pytest.mark.django_db
class TestPageModel:
    def test_create_page(self, org, admin_user):
        page = Page.objects.create(name="Test Page", content="# Hello", organization=org, created_by=admin_user)
        assert page.slug == "test-page"
        assert page.guid is not None
        assert page.is_published is True

    def test_soft_delete_page(self, sample_page, admin_user):
        sample_page.soft_delete(user=admin_user)
        assert Page.objects.filter(slug=sample_page.slug).count() == 0
        assert Page.all_objects.filter(slug=sample_page.slug).count() == 1


@pytest.mark.django_db
class TestPageViews:
    def test_page_list_renders(self, admin_client, sample_page):
        response = admin_client.get("/docs/")
        assert response.status_code == 200
        assert sample_page.name in response.content.decode()

    def test_page_list_htmx(self, admin_client, sample_page):
        response = admin_client.get("/docs/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b"page-table" in response.content

    def test_page_list_search(self, admin_client, sample_page):
        response = admin_client.get("/docs/?search=Getting")
        assert response.status_code == 200

    def test_page_list_denied(self, no_perm_client):
        response = no_perm_client.get("/docs/")
        assert response.status_code == 403

    def test_page_create(self, admin_client, org):
        response = admin_client.post("/docs/create/", {"name": "New Page", "content": "# New", "is_published": True})
        assert response.status_code == 302
        assert Page.objects.filter(slug="new-page").exists()

    def test_page_create_denied(self, no_perm_client, org):
        response = no_perm_client.post("/docs/create/", {"name": "Hack"})
        assert response.status_code == 403

    def test_page_detail_renders_markdown(self, admin_client, sample_page):
        response = admin_client.get(f"/docs/{sample_page.slug}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "<h1>" in content or "Getting Started" in content

    def test_page_detail_denied(self, no_perm_client, sample_page):
        response = no_perm_client.get(f"/docs/{sample_page.slug}/")
        assert response.status_code == 403

    def test_page_update(self, admin_client, sample_page):
        response = admin_client.post(
            f"/docs/{sample_page.slug}/edit/",
            {"name": "Updated Page", "content": "# Updated", "is_published": True},
        )
        assert response.status_code == 302
        sample_page.refresh_from_db()
        assert sample_page.name == "Updated Page"

    def test_page_update_denied(self, no_perm_client, sample_page):
        response = no_perm_client.post(f"/docs/{sample_page.slug}/edit/", {"name": "Hacked"})
        assert response.status_code == 403

    def test_page_delete(self, admin_client, sample_page):
        response = admin_client.post(f"/docs/{sample_page.slug}/delete/")
        assert response.status_code == 302
        assert Page.objects.filter(slug=sample_page.slug).count() == 0

    def test_page_delete_denied(self, no_perm_client, sample_page):
        response = no_perm_client.post(f"/docs/{sample_page.slug}/delete/")
        assert response.status_code == 403

    def test_draft_page_visible_to_admin(self, admin_client, org, admin_user):
        Page.objects.create(name="Draft Doc", content="Secret", organization=org, is_published=False, created_by=admin_user)
        response = admin_client.get("/docs/")
        assert "Draft Doc" in response.content.decode()

    def test_draft_page_hidden_from_viewer(self, viewer_client, org, admin_user):
        Page.objects.create(name="Draft Doc", content="Secret", organization=org, is_published=False, created_by=admin_user)
        response = viewer_client.get("/docs/")
        assert "Draft Doc" not in response.content.decode()

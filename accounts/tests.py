import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestLogin:
    def test_login_page_renders(self, client):
        response = client.get(reverse("accounts:login"))
        assert response.status_code == 200
        assert b"Sign in" in response.content

    def test_login_success_redirects_to_dashboard(self, client, admin_user):
        response = client.post(reverse("accounts:login"), {"username": "admin", "password": "testpass123"})
        assert response.status_code == 302
        assert response.url == reverse("dashboard")

    def test_login_failure_shows_error(self, client, admin_user):
        response = client.post(reverse("accounts:login"), {"username": "admin", "password": "wrong"})
        assert response.status_code == 200
        assert b"Invalid username or password" in response.content

    def test_login_redirect_when_already_authenticated(self, admin_client):
        response = admin_client.get(reverse("accounts:login"))
        assert response.status_code == 302

    def test_login_with_next_param(self, client, admin_user):
        response = client.post(reverse("accounts:login") + "?next=/projects/", {"username": "admin", "password": "testpass123"})
        assert response.status_code == 302
        assert response.url == "/projects/"


@pytest.mark.django_db
class TestLogout:
    def test_logout_redirects_to_login(self, admin_client):
        response = admin_client.post(reverse("accounts:logout"))
        assert response.status_code == 302
        assert reverse("accounts:login") in response.url

    def test_logout_clears_session(self, admin_client):
        admin_client.post(reverse("accounts:logout"))
        response = admin_client.get(reverse("dashboard"))
        assert response.status_code == 302  # redirected to login

    def test_logout_rejects_get(self, admin_client):
        response = admin_client.get(reverse("accounts:logout"))
        assert response.status_code == 405

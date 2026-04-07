import pytest
from django.urls import reverse

from accounts.models import PersonalAccessToken, UserProfile


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


# ---------------------------------------------------------------------------
# Profile views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProfile:
    def test_profile_page_renders(self, admin_client, admin_user):
        response = admin_client.get(reverse("accounts:profile"))
        assert response.status_code == 200
        assert b"Profile Info" in response.content
        assert b"SSH Keys" in response.content
        assert b"Personal Access Tokens" in response.content

    def test_profile_creates_user_profile_on_first_visit(self, admin_client, admin_user):
        assert not UserProfile.objects.filter(user=admin_user).exists()
        admin_client.get(reverse("accounts:profile"))
        assert UserProfile.objects.filter(user=admin_user).exists()

    def test_profile_requires_login(self, client):
        response = client.get(reverse("accounts:profile"))
        assert response.status_code == 302
        assert "/auth/login/" in response.url

    def test_profile_top_level_redirect(self, admin_client):
        response = admin_client.get("/profile/")
        assert response.status_code == 302
        assert "/auth/profile/" in response.url


@pytest.mark.django_db
class TestProfileEdit:
    def test_edit_page_renders(self, admin_client, admin_user):
        response = admin_client.get(reverse("accounts:profile_edit"))
        assert response.status_code == 200
        assert b"Edit Profile" in response.content

    def test_edit_updates_user_fields(self, admin_client, admin_user):
        response = admin_client.post(
            reverse("accounts:profile_edit"),
            {
                "first_name": "Alice",
                "last_name": "Smith",
                "email": "alice@example.com",
                "handle": "alice-s",
                "bio": "Hello world",
                "location": "NYC",
                "website": "https://alice.dev",
            },
        )
        assert response.status_code == 302
        admin_user.refresh_from_db()
        assert admin_user.first_name == "Alice"
        assert admin_user.last_name == "Smith"
        assert admin_user.email == "alice@example.com"
        profile = UserProfile.objects.get(user=admin_user)
        assert profile.handle == "alice-s"
        assert profile.bio == "Hello world"
        assert profile.location == "NYC"
        assert profile.website == "https://alice.dev"

    def test_edit_sanitizes_handle(self, admin_client, admin_user):
        admin_client.post(
            reverse("accounts:profile_edit"),
            {"handle": "  UPPER Case! Stuff  ", "first_name": "", "last_name": "", "email": ""},
        )
        profile = UserProfile.objects.get(user=admin_user)
        assert profile.handle == "uppercasestuff"

    def test_edit_handle_uniqueness(self, admin_client, admin_user, viewer_user):
        # Create a profile with handle for viewer_user
        UserProfile.objects.create(user=viewer_user, handle="taken-handle")
        response = admin_client.post(
            reverse("accounts:profile_edit"),
            {"handle": "taken-handle", "first_name": "", "last_name": "", "email": ""},
        )
        assert response.status_code == 200  # re-renders form with error
        assert b"already taken" in response.content

    def test_edit_empty_handle_saves_as_none(self, admin_client, admin_user):
        admin_client.post(
            reverse("accounts:profile_edit"),
            {"handle": "", "first_name": "", "last_name": "", "email": ""},
        )
        profile = UserProfile.objects.get(user=admin_user)
        assert profile.handle is None

    def test_edit_requires_login(self, client):
        response = client.get(reverse("accounts:profile_edit"))
        assert response.status_code == 302
        assert "/auth/login/" in response.url


@pytest.mark.django_db
class TestPersonalAccessTokenCreate:
    def test_create_form_renders(self, admin_client):
        response = admin_client.get(reverse("accounts:profile_token_create"))
        assert response.status_code == 200
        assert b"Generate Personal Access Token" in response.content

    def test_create_token_shows_raw_once(self, admin_client, admin_user):
        response = admin_client.post(
            reverse("accounts:profile_token_create"),
            {"name": "CI Token", "scopes": "read,write"},
        )
        assert response.status_code == 200
        assert b"frp_" in response.content
        assert b"will not be shown again" in response.content
        token = PersonalAccessToken.objects.get(user=admin_user, name="CI Token")
        assert token.scopes == "read,write"
        assert token.token_prefix.startswith("frp_")

    def test_create_token_default_scope_is_read(self, admin_client, admin_user):
        admin_client.post(
            reverse("accounts:profile_token_create"),
            {"name": "Default Token", "scopes": ""},
        )
        token = PersonalAccessToken.objects.get(user=admin_user, name="Default Token")
        assert token.scopes == "read"

    def test_create_token_rejects_invalid_scopes(self, admin_client, admin_user):
        admin_client.post(
            reverse("accounts:profile_token_create"),
            {"name": "Bad Token", "scopes": "delete,destroy"},
        )
        token = PersonalAccessToken.objects.get(user=admin_user, name="Bad Token")
        assert token.scopes == "read"  # falls back to read

    def test_create_token_requires_name(self, admin_client, admin_user):
        response = admin_client.post(
            reverse("accounts:profile_token_create"),
            {"name": "", "scopes": "read"},
        )
        assert response.status_code == 200
        assert b"Token name is required" in response.content
        assert PersonalAccessToken.objects.filter(user=admin_user).count() == 0

    def test_create_token_requires_login(self, client):
        response = client.get(reverse("accounts:profile_token_create"))
        assert response.status_code == 302
        assert "/auth/login/" in response.url


@pytest.mark.django_db
class TestPersonalAccessTokenRevoke:
    def test_revoke_token(self, admin_client, admin_user):
        raw, token_hash, prefix = PersonalAccessToken.generate()
        token = PersonalAccessToken.objects.create(user=admin_user, name="To Revoke", token_hash=token_hash, token_prefix=prefix)
        response = admin_client.post(reverse("accounts:profile_token_revoke", kwargs={"guid": prefix}))
        assert response.status_code == 302
        token.refresh_from_db()
        assert token.revoked_at is not None

    def test_revoke_token_htmx(self, admin_client, admin_user):
        raw, token_hash, prefix = PersonalAccessToken.generate()
        PersonalAccessToken.objects.create(user=admin_user, name="HX Revoke", token_hash=token_hash, token_prefix=prefix)
        response = admin_client.post(
            reverse("accounts:profile_token_revoke", kwargs={"guid": prefix}),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert response["HX-Redirect"] == "/auth/profile/"

    def test_revoke_token_wrong_user(self, admin_client, viewer_user):
        """Cannot revoke another user's token."""
        raw, token_hash, prefix = PersonalAccessToken.generate()
        PersonalAccessToken.objects.create(user=viewer_user, name="Other User", token_hash=token_hash, token_prefix=prefix)
        response = admin_client.post(reverse("accounts:profile_token_revoke", kwargs={"guid": prefix}))
        assert response.status_code == 404

    def test_revoke_already_revoked(self, admin_client, admin_user):
        from django.utils import timezone

        raw, token_hash, prefix = PersonalAccessToken.generate()
        PersonalAccessToken.objects.create(
            user=admin_user, name="Already Revoked", token_hash=token_hash, token_prefix=prefix, revoked_at=timezone.now()
        )
        response = admin_client.post(reverse("accounts:profile_token_revoke", kwargs={"guid": prefix}))
        assert response.status_code == 404

    def test_revoke_requires_post(self, admin_client, admin_user):
        raw, token_hash, prefix = PersonalAccessToken.generate()
        PersonalAccessToken.objects.create(user=admin_user, name="GET test", token_hash=token_hash, token_prefix=prefix)
        response = admin_client.get(reverse("accounts:profile_token_revoke", kwargs={"guid": prefix}))
        assert response.status_code == 405

    def test_revoke_requires_login(self, client):
        response = client.post(reverse("accounts:profile_token_revoke", kwargs={"guid": "frp_xxxxxxx"}))
        assert response.status_code == 302
        assert "/auth/login/" in response.url


# ---------------------------------------------------------------------------
# Model unit tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserProfileModel:
    def test_str_with_handle(self, admin_user):
        profile = UserProfile.objects.create(user=admin_user, handle="testhandle")
        assert str(profile) == "@testhandle"

    def test_str_without_handle(self, admin_user):
        profile = UserProfile.objects.create(user=admin_user)
        assert str(profile) == "@admin"

    def test_sanitize_handle(self):
        assert UserProfile.sanitize_handle("Hello World!") == "helloworld"
        assert UserProfile.sanitize_handle("  --test-handle--  ") == "test-handle"
        assert UserProfile.sanitize_handle("UPPER_CASE") == "uppercase"
        assert UserProfile.sanitize_handle("") == ""

    def test_multiple_null_handles_allowed(self, admin_user, viewer_user):
        """Multiple profiles with handle=None should not violate unique constraint."""
        UserProfile.objects.create(user=admin_user, handle=None)
        UserProfile.objects.create(user=viewer_user, handle=None)
        assert UserProfile.objects.filter(handle__isnull=True).count() == 2


@pytest.mark.django_db
class TestPersonalAccessTokenModel:
    def test_generate_returns_triple(self):
        raw, hash_val, prefix = PersonalAccessToken.generate()
        assert raw.startswith("frp_")
        assert len(hash_val) == 64
        assert prefix == raw[:12]

    def test_hash_token_matches_generate(self):
        raw, expected_hash, _ = PersonalAccessToken.generate()
        assert PersonalAccessToken.hash_token(raw) == expected_hash

    def test_is_expired(self, admin_user):
        from django.utils import timezone

        token = PersonalAccessToken(user=admin_user, expires_at=timezone.now() - timezone.timedelta(days=1))
        assert token.is_expired is True

    def test_is_not_expired(self, admin_user):
        from django.utils import timezone

        token = PersonalAccessToken(user=admin_user, expires_at=timezone.now() + timezone.timedelta(days=1))
        assert token.is_expired is False

    def test_is_active(self, admin_user):
        token = PersonalAccessToken(user=admin_user)
        assert token.is_active is True

    def test_is_revoked(self, admin_user):
        from django.utils import timezone

        token = PersonalAccessToken(user=admin_user, revoked_at=timezone.now())
        assert token.is_active is False
        assert token.is_revoked is True

    def test_str(self, admin_user):
        token = PersonalAccessToken(user=admin_user, name="Test", token_prefix="frp_abc12345")
        assert str(token) == "Test (frp_abc12345...)"

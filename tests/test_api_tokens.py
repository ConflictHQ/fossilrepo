import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.api_tokens import APIToken, authenticate_api_token
from fossil.models import FossilRepository
from organization.models import Team
from projects.models import ProjectTeam


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def api_token(fossil_repo_obj, admin_user):
    """Create an API token and return (APIToken instance, raw_token)."""
    raw, token_hash, prefix = APIToken.generate()
    token = APIToken.objects.create(
        repository=fossil_repo_obj,
        name="Test Token",
        token_hash=token_hash,
        token_prefix=prefix,
        permissions="status:write",
        created_by=admin_user,
    )
    return token, raw


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """User with write access but not admin."""
    writer = User.objects.create_user(username="writer_tok", password="testpass123")
    team = Team.objects.create(name="Token Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="writer_tok", password="testpass123")
    return client


# --- APIToken Model Tests ---


@pytest.mark.django_db
class TestAPITokenModel:
    def test_generate_token(self):
        raw, token_hash, prefix = APIToken.generate()
        assert raw.startswith("frp_")
        assert len(token_hash) == 64  # SHA-256 hex digest
        assert prefix == raw[:12]

    def test_hash_token(self):
        raw, token_hash, prefix = APIToken.generate()
        assert APIToken.hash_token(raw) == token_hash

    def test_create_token(self, api_token):
        token, raw = api_token
        assert token.pk is not None
        assert "Test Token" in str(token)
        assert token.token_prefix in str(token)

    def test_soft_delete(self, api_token, admin_user):
        token, _ = api_token
        token.soft_delete(user=admin_user)
        assert token.is_deleted
        assert APIToken.objects.filter(pk=token.pk).count() == 0
        assert APIToken.all_objects.filter(pk=token.pk).count() == 1

    def test_has_permission(self, api_token):
        token, _ = api_token
        assert token.has_permission("status:write") is True
        assert token.has_permission("status:read") is False

    def test_has_permission_wildcard(self, fossil_repo_obj, admin_user):
        raw, token_hash, prefix = APIToken.generate()
        token = APIToken.objects.create(
            repository=fossil_repo_obj,
            name="Wildcard",
            token_hash=token_hash,
            token_prefix=prefix,
            permissions="*",
            created_by=admin_user,
        )
        assert token.has_permission("status:write") is True
        assert token.has_permission("anything") is True

    def test_unique_token_hash(self, fossil_repo_obj, admin_user, api_token):
        """Token hashes must be unique across all tokens."""
        token, _ = api_token
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            APIToken.objects.create(
                repository=fossil_repo_obj,
                name="Duplicate Hash",
                token_hash=token.token_hash,
                token_prefix="dup_",
                created_by=admin_user,
            )


# --- authenticate_api_token Tests ---


@pytest.mark.django_db
class TestAuthenticateAPIToken:
    def test_valid_token(self, api_token, fossil_repo_obj):
        token, raw = api_token

        class FakeRequest:
            META = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

        result = authenticate_api_token(FakeRequest(), fossil_repo_obj)
        assert result is not None
        assert result.pk == token.pk

    def test_invalid_token(self, fossil_repo_obj):
        class FakeRequest:
            META = {"HTTP_AUTHORIZATION": "Bearer invalid_token_xyz"}

        result = authenticate_api_token(FakeRequest(), fossil_repo_obj)
        assert result is None

    def test_no_auth_header(self, fossil_repo_obj):
        class FakeRequest:
            META = {}

        result = authenticate_api_token(FakeRequest(), fossil_repo_obj)
        assert result is None

    def test_non_bearer_auth(self, fossil_repo_obj):
        class FakeRequest:
            META = {"HTTP_AUTHORIZATION": "Basic dXNlcjpwYXNz"}

        result = authenticate_api_token(FakeRequest(), fossil_repo_obj)
        assert result is None

    def test_expired_token(self, fossil_repo_obj, admin_user):
        from datetime import timedelta

        from django.utils import timezone

        raw, token_hash, prefix = APIToken.generate()
        APIToken.objects.create(
            repository=fossil_repo_obj,
            name="Expired",
            token_hash=token_hash,
            token_prefix=prefix,
            expires_at=timezone.now() - timedelta(days=1),
            created_by=admin_user,
        )

        class FakeRequest:
            META = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

        result = authenticate_api_token(FakeRequest(), fossil_repo_obj)
        assert result is None

    def test_updates_last_used_at(self, api_token, fossil_repo_obj):
        token, raw = api_token
        assert token.last_used_at is None

        class FakeRequest:
            META = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

        authenticate_api_token(FakeRequest(), fossil_repo_obj)
        token.refresh_from_db()
        assert token.last_used_at is not None

    def test_deleted_token_not_found(self, api_token, fossil_repo_obj, admin_user):
        token, raw = api_token
        token.soft_delete(user=admin_user)

        class FakeRequest:
            META = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

        result = authenticate_api_token(FakeRequest(), fossil_repo_obj)
        assert result is None


# --- API Token List View Tests ---


@pytest.mark.django_db
class TestAPITokenListView:
    def test_list_tokens(self, admin_client, sample_project, api_token):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tokens/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Test Token" in content
        assert "status:write" in content

    def test_list_empty(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tokens/")
        assert response.status_code == 200
        assert "No API tokens generated yet" in response.content.decode()

    def test_list_denied_for_writer(self, writer_client, sample_project, api_token):
        """Token management requires admin, not just write."""
        response = writer_client.get(f"/projects/{sample_project.slug}/fossil/tokens/")
        assert response.status_code == 403

    def test_list_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/tokens/")
        assert response.status_code == 403

    def test_list_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/tokens/")
        assert response.status_code == 302  # redirect to login


# --- API Token Create View Tests ---


@pytest.mark.django_db
class TestAPITokenCreateView:
    def test_get_form(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tokens/create/")
        assert response.status_code == 200
        assert "Generate API Token" in response.content.decode()

    def test_create_token(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tokens/create/",
            {"name": "New CI Token", "permissions": "status:write"},
        )
        assert response.status_code == 200  # Shows the token on the same page
        content = response.content.decode()
        assert "frp_" in content  # Raw token is displayed
        assert "Token Generated" in content

        # Verify token was created in DB
        token = APIToken.objects.get(name="New CI Token")
        assert token.permissions == "status:write"

    def test_create_token_without_name_fails(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tokens/create/",
            {"name": "", "permissions": "status:write"},
        )
        assert response.status_code == 200
        assert "Token name is required" in response.content.decode()
        assert APIToken.objects.filter(repository__project=sample_project).count() == 0

    def test_create_denied_for_writer(self, writer_client, sample_project):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/tokens/create/",
            {"name": "Evil Token"},
        )
        assert response.status_code == 403


# --- API Token Delete View Tests ---


@pytest.mark.django_db
class TestAPITokenDeleteView:
    def test_delete_token(self, admin_client, sample_project, api_token):
        token, _ = api_token
        response = admin_client.post(f"/projects/{sample_project.slug}/fossil/tokens/{token.pk}/delete/")
        assert response.status_code == 302
        token.refresh_from_db()
        assert token.is_deleted

    def test_delete_get_redirects(self, admin_client, sample_project, api_token):
        token, _ = api_token
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tokens/{token.pk}/delete/")
        assert response.status_code == 302  # GET redirects to list

    def test_delete_denied_for_writer(self, writer_client, sample_project, api_token):
        token, _ = api_token
        response = writer_client.post(f"/projects/{sample_project.slug}/fossil/tokens/{token.pk}/delete/")
        assert response.status_code == 403

    def test_delete_nonexistent_token(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(f"/projects/{sample_project.slug}/fossil/tokens/99999/delete/")
        assert response.status_code == 404

    def test_deleted_token_cannot_be_deleted_again(self, admin_client, sample_project, api_token, admin_user):
        token, _ = api_token
        token.soft_delete(user=admin_user)
        response = admin_client.post(f"/projects/{sample_project.slug}/fossil/tokens/{token.pk}/delete/")
        assert response.status_code == 404

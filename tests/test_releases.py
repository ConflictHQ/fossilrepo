import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from fossil.models import FossilRepository
from fossil.releases import Release, ReleaseAsset

# File storage settings for tests -- the project only configures STORAGES["default"]
# when USE_S3=true, so tests that use FileField need a local filesystem backend.
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def release(fossil_repo_obj, admin_user):
    return Release.objects.create(
        repository=fossil_repo_obj,
        tag_name="v1.0.0",
        name="Version 1.0.0",
        body="## Changelog\n\n- Initial release",
        is_prerelease=False,
        is_draft=False,
        published_at="2026-04-01T00:00:00Z",
        created_by=admin_user,
    )


@pytest.fixture
def draft_release(fossil_repo_obj, admin_user):
    return Release.objects.create(
        repository=fossil_repo_obj,
        tag_name="v2.0.0-beta",
        name="Version 2.0.0 Beta",
        body="Draft notes",
        is_prerelease=True,
        is_draft=True,
        published_at=None,
        created_by=admin_user,
    )


@pytest.fixture
def release_asset(release, admin_user, tmp_path, settings):
    settings.STORAGES = _TEST_STORAGES
    settings.MEDIA_ROOT = str(tmp_path / "media")
    uploaded = SimpleUploadedFile("app-v1.0.0.tar.gz", b"fake-tarball-content", content_type="application/gzip")
    return ReleaseAsset.objects.create(
        release=release,
        name="app-v1.0.0.tar.gz",
        file=uploaded,
        file_size_bytes=len(b"fake-tarball-content"),
        content_type="application/gzip",
        created_by=admin_user,
    )


@pytest.mark.django_db
class TestReleaseModel:
    def test_create_release(self, release):
        assert release.pk is not None
        assert str(release) == "v1.0.0: Version 1.0.0"

    def test_unique_tag_per_repo(self, fossil_repo_obj, admin_user):
        Release.objects.create(
            repository=fossil_repo_obj,
            tag_name="v3.0.0",
            name="First",
            created_by=admin_user,
        )
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            Release.objects.create(
                repository=fossil_repo_obj,
                tag_name="v3.0.0",
                name="Duplicate",
                created_by=admin_user,
            )

    def test_soft_delete(self, release, admin_user):
        release.soft_delete(user=admin_user)
        assert release.is_deleted
        assert Release.objects.filter(pk=release.pk).count() == 0
        assert Release.all_objects.filter(pk=release.pk).count() == 1

    def test_ordering(self, fossil_repo_obj, admin_user):
        r1 = Release.objects.create(
            repository=fossil_repo_obj,
            tag_name="v0.1.0",
            name="Old",
            published_at="2025-01-01T00:00:00Z",
            created_by=admin_user,
        )
        r2 = Release.objects.create(
            repository=fossil_repo_obj,
            tag_name="v0.2.0",
            name="Newer",
            published_at="2026-06-01T00:00:00Z",
            created_by=admin_user,
        )
        releases = list(Release.objects.filter(repository=fossil_repo_obj))
        assert releases[0] == r2
        assert releases[-1] == r1


@pytest.mark.django_db
class TestReleaseAssetModel:
    def test_create_asset(self, release_asset):
        assert release_asset.pk is not None
        assert str(release_asset) == "app-v1.0.0.tar.gz"
        assert release_asset.file_size_bytes == len(b"fake-tarball-content")

    def test_soft_delete(self, release_asset, admin_user):
        release_asset.soft_delete(user=admin_user)
        assert release_asset.is_deleted
        assert ReleaseAsset.objects.filter(pk=release_asset.pk).count() == 0
        assert ReleaseAsset.all_objects.filter(pk=release_asset.pk).count() == 1


@pytest.mark.django_db
class TestReleaseListView:
    def test_list_releases(self, admin_client, sample_project, release):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/releases/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "v1.0.0" in content
        assert "Version 1.0.0" in content

    def test_list_empty(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/releases/")
        assert response.status_code == 200
        assert "No releases yet" in response.content.decode()

    def test_drafts_hidden_from_non_writers(self, no_perm_client, sample_project, draft_release):
        # Make project public so no_perm_user can read it
        sample_project.visibility = "public"
        sample_project.save()
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/releases/")
        assert response.status_code == 200
        assert "v2.0.0-beta" not in response.content.decode()

    def test_drafts_visible_to_writers(self, admin_client, sample_project, draft_release):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/releases/")
        assert response.status_code == 200
        assert "v2.0.0-beta" in response.content.decode()

    def test_list_denied_for_no_perm_on_private(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/releases/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestReleaseDetailView:
    def test_detail(self, admin_client, sample_project, release):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "v1.0.0" in content
        assert "Changelog" in content

    def test_detail_with_assets(self, admin_client, sample_project, release, release_asset):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "app-v1.0.0.tar.gz" in content
        assert "Download" in content

    def test_draft_detail_denied_for_non_writer(self, no_perm_client, sample_project, draft_release):
        sample_project.visibility = "public"
        sample_project.save()
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/releases/{draft_release.tag_name}/")
        assert response.status_code == 403

    def test_detail_denied_for_no_perm_on_private(self, no_perm_client, sample_project, release):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestReleaseCreateView:
    def test_get_form(self, admin_client, sample_project):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/releases/create/")
        assert response.status_code == 200
        assert "Create Release" in response.content.decode()

    def test_create_release(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/releases/create/",
            {"tag_name": "v5.0.0", "name": "Big Release", "body": "notes", "is_prerelease": "", "is_draft": ""},
        )
        assert response.status_code == 302
        release = Release.objects.get(tag_name="v5.0.0")
        assert release.name == "Big Release"
        assert release.published_at is not None
        assert release.is_draft is False

    def test_create_draft_release(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/releases/create/",
            {"tag_name": "v6.0.0-rc1", "name": "RC", "body": "", "is_draft": "on"},
        )
        assert response.status_code == 302
        release = Release.objects.get(tag_name="v6.0.0-rc1")
        assert release.is_draft is True
        assert release.published_at is None

    def test_create_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.post(
            f"/projects/{sample_project.slug}/fossil/releases/create/",
            {"tag_name": "v9.0.0", "name": "Nope"},
        )
        assert response.status_code == 403

    def test_create_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/releases/create/")
        assert response.status_code == 302  # redirect to login


@pytest.mark.django_db
class TestReleaseEditView:
    def test_get_edit_form(self, admin_client, sample_project, release):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "v1.0.0" in content
        assert "Update Release" in content

    def test_edit_release(self, admin_client, sample_project, release):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/edit/",
            {"tag_name": "v1.0.1", "name": "Patched", "body": "fix", "is_prerelease": ""},
        )
        assert response.status_code == 302
        release.refresh_from_db()
        assert release.tag_name == "v1.0.1"
        assert release.name == "Patched"

    def test_edit_denied_for_no_perm(self, no_perm_client, sample_project, release):
        response = no_perm_client.post(
            f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/edit/",
            {"tag_name": "v1.0.1", "name": "Hacked"},
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestReleaseDeleteView:
    def test_delete_release(self, admin_client, sample_project, release):
        response = admin_client.post(f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/delete/")
        assert response.status_code == 302
        release.refresh_from_db()
        assert release.is_deleted

    def test_delete_get_redirects(self, admin_client, sample_project, release):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/delete/")
        assert response.status_code == 302  # GET redirects to detail

    def test_delete_denied_for_writer(self, admin_client, sample_project, release, admin_user):
        """Delete requires admin, not just write. Admin user is superuser so they have admin.
        We test with a write-only user instead."""
        from django.contrib.auth.models import User

        from organization.models import Team
        from projects.models import ProjectTeam

        writer = User.objects.create_user(username="writer", password="testpass123")
        team = Team.objects.create(name="Writers", organization=sample_project.organization, created_by=admin_user)
        team.members.add(writer)
        ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)

        from django.test import Client

        writer_client = Client()
        writer_client.login(username="writer", password="testpass123")

        response = writer_client.post(f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/delete/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestReleaseAssetUploadView:
    def test_upload_asset(self, admin_client, sample_project, release, tmp_path, settings):
        settings.STORAGES = _TEST_STORAGES
        settings.MEDIA_ROOT = str(tmp_path / "media")
        fake_file = SimpleUploadedFile("binary.zip", b"zipdata", content_type="application/zip")
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/upload/",
            {"file": fake_file},
        )
        assert response.status_code == 302
        asset = ReleaseAsset.objects.get(release=release, name="binary.zip")
        assert asset.file_size_bytes == len(b"zipdata")
        assert asset.content_type == "application/zip"

    def test_upload_denied_for_no_perm(self, no_perm_client, sample_project, release):
        fake_file = SimpleUploadedFile("evil.zip", b"data", content_type="application/zip")
        response = no_perm_client.post(
            f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/upload/",
            {"file": fake_file},
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestReleaseAssetDownloadView:
    def test_download_asset(self, admin_client, sample_project, release, release_asset):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/assets/{release_asset.pk}/")
        assert response.status_code == 200
        assert response["Content-Disposition"]
        # Verify download count incremented
        release_asset.refresh_from_db()
        assert release_asset.download_count == 1

    def test_download_increments_count(self, admin_client, sample_project, release, release_asset):
        url = f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/assets/{release_asset.pk}/"
        admin_client.get(url)
        admin_client.get(url)
        release_asset.refresh_from_db()
        assert release_asset.download_count == 2

    def test_download_denied_on_private_for_no_perm(self, no_perm_client, sample_project, release, release_asset):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/releases/{release.tag_name}/assets/{release_asset.pk}/")
        assert response.status_code == 403

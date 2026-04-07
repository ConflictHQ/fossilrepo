from unittest.mock import MagicMock, patch

import pytest

from fossil.models import FossilRepository

# Reusable patch that makes FossilRepository.exists_on_disk return True
_disk_patch = patch("fossil.models.FossilRepository.exists_on_disk", new_callable=lambda: property(lambda self: True))


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


def _make_reader_mock(**methods):
    """Create a MagicMock that replaces FossilReader as a class.

    The returned mock supports:
        reader = FossilReader(path)   # returns a mock instance
        with reader:                  # context manager
            reader.some_method()      # returns configured value
    """
    mock_cls = MagicMock()
    instance = MagicMock()
    mock_cls.return_value = instance
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    for name, val in methods.items():
        getattr(instance, name).return_value = val
    return mock_cls


@pytest.mark.django_db
class TestUnversionedListView:
    def test_list_page_loads(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock(
            get_unversioned_files=[
                {"name": "release.tar.gz", "size": 1024, "mtime": None, "hash": "abc"},
                {"name": "readme.txt", "size": 42, "mtime": None, "hash": "def"},
            ]
        )
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/files/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Unversioned Files" in content
        assert "release.tar.gz" in content
        assert "readme.txt" in content

    def test_list_empty(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock(get_unversioned_files=[])
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/files/")
        assert response.status_code == 200
        assert "No unversioned files" in response.content.decode()

    def test_list_shows_upload_for_admin(self, admin_client, sample_project, fossil_repo_obj):
        mock = _make_reader_mock(get_unversioned_files=[])
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/files/")
        assert response.status_code == 200
        assert "Upload File" in response.content.decode()

    def test_list_hides_upload_for_non_admin(self, sample_project, fossil_repo_obj):
        """A user with write but not admin access should not see the upload form."""
        from django.contrib.auth.models import User
        from django.test import Client

        from organization.models import Team
        from projects.models import ProjectTeam

        writer = User.objects.create_user(username="writer_only", password="testpass123")
        team = Team.objects.create(name="Writers", organization=sample_project.organization, created_by=writer)
        team.members.add(writer)
        ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=writer)

        c = Client()
        c.login(username="writer_only", password="testpass123")
        mock = _make_reader_mock(get_unversioned_files=[])
        with _disk_patch, patch("fossil.views.FossilReader", mock):
            response = c.get(f"/projects/{sample_project.slug}/fossil/files/")
        assert response.status_code == 200
        assert "Upload File" not in response.content.decode()

    def test_list_denied_for_no_perm_on_private(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/files/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestUnversionedDownloadView:
    def test_download_file(self, admin_client, sample_project, fossil_repo_obj):
        mock_cli = MagicMock()
        mock_cli.return_value.uv_cat.return_value = b"file content here"
        with _disk_patch, patch("fossil.cli.FossilCLI", mock_cli):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/files/download/readme.txt")
        assert response.status_code == 200
        assert response.content == b"file content here"
        assert response["Content-Disposition"] == 'attachment; filename="readme.txt"'

    def test_download_nested_path(self, admin_client, sample_project, fossil_repo_obj):
        mock_cli = MagicMock()
        mock_cli.return_value.uv_cat.return_value = b"tarball bytes"
        with _disk_patch, patch("fossil.cli.FossilCLI", mock_cli):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/files/download/dist/app-v1.0.tar.gz")
        assert response.status_code == 200
        assert response["Content-Disposition"] == 'attachment; filename="app-v1.0.tar.gz"'

    def test_download_not_found(self, admin_client, sample_project, fossil_repo_obj):
        mock_cli = MagicMock()
        mock_cli.return_value.uv_cat.side_effect = FileNotFoundError("Not found")
        with _disk_patch, patch("fossil.cli.FossilCLI", mock_cli):
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/files/download/missing.txt")
        assert response.status_code == 404

    def test_download_denied_for_no_perm_on_private(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/files/download/secret.txt")
        assert response.status_code == 403


@pytest.mark.django_db
class TestUnversionedUploadView:
    def test_upload_file(self, admin_client, sample_project, fossil_repo_obj):
        from django.core.files.uploadedfile import SimpleUploadedFile

        uploaded = SimpleUploadedFile("artifact.bin", b"binary content", content_type="application/octet-stream")
        mock_cli = MagicMock()
        mock_cli.return_value.uv_add.return_value = True
        with _disk_patch, patch("fossil.cli.FossilCLI", mock_cli):
            response = admin_client.post(
                f"/projects/{sample_project.slug}/fossil/files/upload/",
                {"file": uploaded},
            )
        assert response.status_code == 302  # Redirect to list
        mock_cli.return_value.uv_add.assert_called_once()

    def test_upload_no_file(self, admin_client, sample_project, fossil_repo_obj):
        with _disk_patch:
            response = admin_client.post(f"/projects/{sample_project.slug}/fossil/files/upload/")
        assert response.status_code == 302  # Redirect back with error

    def test_upload_get_redirects(self, admin_client, sample_project, fossil_repo_obj):
        with _disk_patch:
            response = admin_client.get(f"/projects/{sample_project.slug}/fossil/files/upload/")
        assert response.status_code == 302

    def test_upload_denied_for_anon(self, client, sample_project):
        response = client.post(f"/projects/{sample_project.slug}/fossil/files/upload/")
        assert response.status_code == 302  # Redirect to login

    def test_upload_denied_for_writer(self, sample_project, fossil_repo_obj):
        """Upload requires admin, not just write access."""
        from django.contrib.auth.models import User
        from django.test import Client

        from organization.models import Team
        from projects.models import ProjectTeam

        writer = User.objects.create_user(username="writer_upl", password="testpass123")
        team = Team.objects.create(name="UplWriters", organization=sample_project.organization, created_by=writer)
        team.members.add(writer)
        ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=writer)

        c = Client()
        c.login(username="writer_upl", password="testpass123")
        from django.core.files.uploadedfile import SimpleUploadedFile

        uploaded = SimpleUploadedFile("hack.bin", b"nope", content_type="application/octet-stream")
        response = c.post(
            f"/projects/{sample_project.slug}/fossil/files/upload/",
            {"file": uploaded},
        )
        assert response.status_code == 403

    def test_upload_denied_for_no_perm(self, no_perm_client, sample_project):
        from django.core.files.uploadedfile import SimpleUploadedFile

        uploaded = SimpleUploadedFile("hack.bin", b"nope", content_type="application/octet-stream")
        response = no_perm_client.post(
            f"/projects/{sample_project.slug}/fossil/files/upload/",
            {"file": uploaded},
        )
        assert response.status_code == 403

"""Tests for the fossilrepo-ctl bundle export/import commands."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ctl.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.django_db
class TestBundleExport:
    def test_export_missing_project(self, runner):
        """Export with a non-existent project slug prints an error."""
        result = runner.invoke(cli, ["bundle", "export", "nonexistent-project", "/tmp/out.bundle"])
        assert result.exit_code == 0
        assert "No repository found" in result.output

    def test_export_repo_not_on_disk(self, runner, sample_project):
        """Export when the .fossil file does not exist on disk."""
        result = runner.invoke(cli, ["bundle", "export", sample_project.slug, "/tmp/out.bundle"])
        assert result.exit_code == 0
        assert "not found on disk" in result.output

    def test_export_fossil_not_available(self, runner, sample_project):
        """Export when fossil binary is not found."""
        from fossil.models import FossilRepository

        repo = FossilRepository.objects.get(project=sample_project)

        with (
            patch.object(type(repo), "exists_on_disk", new_callable=lambda: property(lambda self: True)),
            patch("fossil.cli.FossilCLI.is_available", return_value=False),
        ):
            result = runner.invoke(cli, ["bundle", "export", sample_project.slug, "/tmp/out.bundle"])
            assert result.exit_code == 0
            assert "Fossil binary not found" in result.output

    def test_export_success(self, runner, sample_project, tmp_path):
        """Export succeeds when fossil binary is available and returns 0."""
        from fossil.models import FossilRepository

        repo = FossilRepository.objects.get(project=sample_project)
        output_path = tmp_path / "test.bundle"

        mock_run = MagicMock()
        mock_run.returncode = 0
        mock_run.stdout = ""
        mock_run.stderr = ""

        with (
            patch.object(type(repo), "exists_on_disk", new_callable=lambda: property(lambda self: True)),
            patch("fossil.cli.FossilCLI.is_available", return_value=True),
            patch("subprocess.run", return_value=mock_run),
        ):
            # Create a fake output file so size calculation works
            output_path.write_bytes(b"x" * 1024)
            result = runner.invoke(cli, ["bundle", "export", sample_project.slug, str(output_path)])
            assert result.exit_code == 0
            assert "Success" in result.output

    def test_export_failure(self, runner, sample_project, tmp_path):
        """Export reports failure when fossil returns non-zero."""
        from fossil.models import FossilRepository

        repo = FossilRepository.objects.get(project=sample_project)
        output_path = tmp_path / "test.bundle"

        mock_run = MagicMock()
        mock_run.returncode = 1
        mock_run.stdout = ""
        mock_run.stderr = "bundle export failed"

        with (
            patch.object(type(repo), "exists_on_disk", new_callable=lambda: property(lambda self: True)),
            patch("fossil.cli.FossilCLI.is_available", return_value=True),
            patch("subprocess.run", return_value=mock_run),
        ):
            result = runner.invoke(cli, ["bundle", "export", sample_project.slug, str(output_path)])
            assert result.exit_code == 0
            assert "Failed" in result.output


@pytest.mark.django_db
class TestBundleImport:
    def test_import_missing_project(self, runner):
        """Import with a non-existent project slug prints an error."""
        result = runner.invoke(cli, ["bundle", "import", "nonexistent-project", "/tmp/in.bundle"])
        assert result.exit_code == 0
        assert "No repository found" in result.output

    def test_import_bundle_file_not_found(self, runner, sample_project):
        """Import when the bundle file does not exist."""
        from fossil.models import FossilRepository

        repo = FossilRepository.objects.get(project=sample_project)

        with patch.object(type(repo), "exists_on_disk", new_callable=lambda: property(lambda self: True)):
            result = runner.invoke(cli, ["bundle", "import", sample_project.slug, "/tmp/definitely-not-a-file.bundle"])
            assert result.exit_code == 0
            assert "not found" in result.output.lower()

    def test_import_success(self, runner, sample_project, tmp_path):
        """Import succeeds when fossil binary is available and returns 0."""
        from fossil.models import FossilRepository

        repo = FossilRepository.objects.get(project=sample_project)
        bundle_file = tmp_path / "test.bundle"
        bundle_file.write_bytes(b"fake-bundle")

        mock_run = MagicMock()
        mock_run.returncode = 0
        mock_run.stdout = "imported 42 artifacts"
        mock_run.stderr = ""

        with (
            patch.object(type(repo), "exists_on_disk", new_callable=lambda: property(lambda self: True)),
            patch("fossil.cli.FossilCLI.is_available", return_value=True),
            patch("subprocess.run", return_value=mock_run),
        ):
            result = runner.invoke(cli, ["bundle", "import", sample_project.slug, str(bundle_file)])
            assert result.exit_code == 0
            assert "Success" in result.output

    def test_import_failure(self, runner, sample_project, tmp_path):
        """Import reports failure when fossil returns non-zero."""
        from fossil.models import FossilRepository

        repo = FossilRepository.objects.get(project=sample_project)
        bundle_file = tmp_path / "test.bundle"
        bundle_file.write_bytes(b"fake-bundle")

        mock_run = MagicMock()
        mock_run.returncode = 1
        mock_run.stdout = ""
        mock_run.stderr = "invalid bundle format"

        with (
            patch.object(type(repo), "exists_on_disk", new_callable=lambda: property(lambda self: True)),
            patch("fossil.cli.FossilCLI.is_available", return_value=True),
            patch("subprocess.run", return_value=mock_run),
        ):
            result = runner.invoke(cli, ["bundle", "import", sample_project.slug, str(bundle_file)])
            assert result.exit_code == 0
            assert "Failed" in result.output


class TestBundleCLIGroup:
    def test_bundle_help(self, runner):
        """Bundle group shows help text."""
        result = runner.invoke(cli, ["bundle", "--help"])
        assert result.exit_code == 0
        assert "export" in result.output.lower()
        assert "import" in result.output.lower()

    def test_export_help(self, runner):
        result = runner.invoke(cli, ["bundle", "export", "--help"])
        assert result.exit_code == 0
        assert "PROJECT_SLUG" in result.output

    def test_import_help(self, runner):
        result = runner.invoke(cli, ["bundle", "import", "--help"])
        assert result.exit_code == 0
        assert "PROJECT_SLUG" in result.output

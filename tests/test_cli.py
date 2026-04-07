"""Unit tests for fossil/cli.py -- FossilCLI subprocess wrapper.

Tests mock subprocess.run throughout since FossilCLI is a thin wrapper
around the fossil binary.  We verify that:
- Correct commands are assembled for every method
- Success/failure return values are propagated correctly
- Environment variables are set properly (_env property)
- Timeouts and exceptions are handled gracefully
- Edge-case inputs (empty strings, special characters) work
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fossil.cli import FossilCLI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(stdout="", stderr="", returncode=0):
    """Build a mock CompletedProcess for a successful command."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _fail(stdout="", stderr="error", returncode=1):
    """Build a mock CompletedProcess for a failed command."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _ok_bytes(stdout=b"", stderr=b"", returncode=0):
    """Build a mock CompletedProcess returning raw bytes (not text)."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Constructor and _env
# ---------------------------------------------------------------------------


class TestFossilCLIInit:
    """Constructor: explicit binary path vs constance fallback."""

    def test_explicit_binary(self):
        cli = FossilCLI(binary="/usr/local/bin/fossil")
        assert cli.binary == "/usr/local/bin/fossil"

    def test_constance_fallback(self):
        mock_config = MagicMock()
        mock_config.FOSSIL_BINARY_PATH = "/opt/fossil/bin/fossil"
        with patch("constance.config", mock_config):
            cli = FossilCLI()
        assert cli.binary == "/opt/fossil/bin/fossil"


class TestEnvProperty:
    """_env injects USER=fossilrepo into the inherited environment."""

    def test_env_sets_user(self):
        cli = FossilCLI(binary="/bin/false")
        env = cli._env
        assert env["USER"] == "fossilrepo"

    def test_env_inherits_system_env(self):
        cli = FossilCLI(binary="/bin/false")
        env = cli._env
        # PATH should come from os.environ
        assert "PATH" in env


# ---------------------------------------------------------------------------
# _run helper
# ---------------------------------------------------------------------------


class TestRunHelper:
    """_run assembles the command and delegates to subprocess.run."""

    def test_run_builds_correct_command(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run", return_value=_ok("ok")) as mock_run:
            cli._run("version")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "version"]

    def test_run_passes_env(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            cli._run("version")
            env = mock_run.call_args[1]["env"]
            assert env["USER"] == "fossilrepo"

    def test_run_uses_check_true(self):
        """_run uses check=True so CalledProcessError is raised on failure."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        with (
            patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "fossil")),
            pytest.raises(subprocess.CalledProcessError),
        ):
            cli._run("bad-command")

    def test_run_custom_timeout(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            cli._run("clone", "http://example.com", timeout=120)
            assert mock_run.call_args[1]["timeout"] == 120

    def test_run_multiple_args(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            cli._run("push", "-R", "/tmp/repo.fossil")
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "push", "-R", "/tmp/repo.fossil"]


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_creates_parent_dirs_and_runs_fossil_init(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        target = tmp_path / "sub" / "dir" / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            result = cli.init(target)
            assert result == target
            # Parent dirs created
            assert target.parent.exists()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "init", str(target)]

    def test_init_returns_path(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        target = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()):
            path = cli.init(target)
            assert isinstance(path, Path)
            assert path == target


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_returns_stripped_stdout(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run", return_value=_ok("  This is fossil version 2.24\n")):
            result = cli.version()
            assert result == "This is fossil version 2.24"

    def test_version_propagates_error(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with (
            patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "fossil")),
            pytest.raises(subprocess.CalledProcessError),
        ):
            cli.version()


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_available_when_version_works(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run", return_value=_ok("2.24")):
            assert cli.is_available() is True

    def test_not_available_on_file_not_found(self):
        cli = FossilCLI(binary="/nonexistent/fossil")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert cli.is_available() is False

    def test_not_available_on_called_process_error(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "fossil")):
            assert cli.is_available() is False


# ---------------------------------------------------------------------------
# render_pikchr
# ---------------------------------------------------------------------------


class TestRenderPikchr:
    def test_renders_svg_on_success(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        svg = '<svg viewBox="0 0 100 100"></svg>'
        result_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=svg, stderr="")
        with patch("subprocess.run", return_value=result_proc) as mock_run:
            result = cli.render_pikchr("circle")
            assert result == svg
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "pikchr", "-"]
            assert mock_run.call_args[1]["input"] == "circle"

    def test_returns_empty_on_failure(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        result_proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
        with patch("subprocess.run", return_value=result_proc):
            assert cli.render_pikchr("bad") == ""

    def test_returns_empty_on_file_not_found(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert cli.render_pikchr("test") == ""

    def test_returns_empty_on_timeout(self):
        cli = FossilCLI(binary="/usr/bin/fossil")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            assert cli.render_pikchr("test") == ""


# ---------------------------------------------------------------------------
# ensure_default_user
# ---------------------------------------------------------------------------


class TestEnsureDefaultUser:
    def test_creates_user_when_missing(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        # First call: user list (user not present), second: create, third: default
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(stdout="admin\n"),  # user list -- "fossilrepo" not in output
                _ok(),  # user new
                _ok(),  # user default
            ]
            cli.ensure_default_user(repo_path)
            assert mock_run.call_count == 3
            # Verify the user new call
            new_cmd = mock_run.call_args_list[1][0][0]
            assert "user" in new_cmd
            assert "new" in new_cmd
            assert "fossilrepo" in new_cmd

    def test_skips_create_when_user_exists(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(stdout="admin\nfossilrepo\n"),  # user list -- fossilrepo IS present
                _ok(),  # user default
            ]
            cli.ensure_default_user(repo_path)
            assert mock_run.call_count == 2  # no "new" call

    def test_custom_username(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(stdout="admin\n"),  # user list -- custom not present
                _ok(),  # user new
                _ok(),  # user default
            ]
            cli.ensure_default_user(repo_path, username="custom-bot")
            new_cmd = mock_run.call_args_list[1][0][0]
            assert "custom-bot" in new_cmd

    def test_silently_swallows_exceptions(self, tmp_path):
        """ensure_default_user has a bare except -- should not raise."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=Exception("kaboom")):
            cli.ensure_default_user(repo_path)  # should not raise


# ---------------------------------------------------------------------------
# tarball
# ---------------------------------------------------------------------------


class TestTarball:
    def test_returns_bytes_on_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        fake_tar = b"\x1f\x8b\x08\x00" + b"\x00" * 100  # fake gzip header
        with patch("subprocess.run", return_value=_ok_bytes(stdout=fake_tar)) as mock_run:
            result = cli.tarball(repo_path, "trunk")
            assert result == fake_tar
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "tarball", "trunk", "-R", str(repo_path), "/dev/stdout"]

    def test_returns_empty_bytes_on_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok_bytes(returncode=1)):
            result = cli.tarball(repo_path, "trunk")
            assert result == b""


# ---------------------------------------------------------------------------
# zip_archive
# ---------------------------------------------------------------------------


class TestZipArchive:
    def test_returns_bytes_on_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        fake_zip = b"PK\x03\x04" + b"\x00" * 100

        def side_effect(cmd, **kwargs):
            # Write content to the tempfile that fossil would create
            # The tempfile path is in the command args
            outfile = cmd[3]  # zip <checkin> <outfile> -R <repo>
            Path(outfile).write_bytes(fake_zip)
            return _ok()

        with patch("subprocess.run", side_effect=side_effect):
            result = cli.zip_archive(repo_path, "trunk")
            assert result == fake_zip

    def test_returns_empty_bytes_on_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.zip_archive(repo_path, "trunk")
            assert result == b""


# ---------------------------------------------------------------------------
# blame
# ---------------------------------------------------------------------------


class TestBlame:
    def test_parses_blame_output(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        blame_output = (
            "abc12345 2026-01-15 ragelink: def hello():\n"
            "abc12345 2026-01-15 ragelink:     return 'world'\n"
            "def67890 2026-01-20 contributor:     pass\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(),  # fossil open
                _ok(stdout=blame_output),  # fossil blame
                _ok(),  # fossil close
            ]
            lines = cli.blame(repo_path, "main.py")
            assert len(lines) == 3
            assert lines[0]["uuid"] == "abc12345"
            assert lines[0]["date"] == "2026-01-15"
            assert lines[0]["user"] == "ragelink"
            assert lines[0]["text"] == "def hello():"
            assert lines[2]["user"] == "contributor"

    def test_returns_empty_on_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(),  # fossil open
                _fail(),  # fossil blame fails
                _ok(),  # fossil close
            ]
            lines = cli.blame(repo_path, "nonexistent.py")
            assert lines == []

    def test_returns_empty_on_exception(self, tmp_path):
        """blame has a broad except -- should not raise."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=Exception("error")):
            lines = cli.blame(repo_path, "file.py")
            assert lines == []

    def test_cleans_up_tmpdir(self, tmp_path):
        """Temp directory must be cleaned up even on error."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"

        created_dirs = []
        original_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = original_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        with (
            patch("subprocess.run", side_effect=Exception("fail")),
            patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp),
        ):
            cli.blame(repo_path, "file.py")

        # The tmpdir should have been cleaned up by shutil.rmtree
        for d in created_dirs:
            assert not Path(d).exists()


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


class TestPush:
    def test_push_success_with_artifacts(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="Round-trips: 1   Artifacts sent: 5   sent: 5")):
            result = cli.push(repo_path)
            assert result["success"] is True
            assert result["artifacts_sent"] == 5

    def test_push_with_remote_url(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="sent: 3")) as mock_run:
            result = cli.push(repo_path, remote_url="https://fossil.example.com/repo")
            cmd = mock_run.call_args[0][0]
            assert "https://fossil.example.com/repo" in cmd
            assert result["artifacts_sent"] == 3

    def test_push_no_artifacts_in_output(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="nothing to push")):
            result = cli.push(repo_path)
            assert result["success"] is True
            assert result["artifacts_sent"] == 0

    def test_push_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail(stdout="connection refused")):
            result = cli.push(repo_path)
            assert result["success"] is False

    def test_push_timeout(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)):
            result = cli.push(repo_path)
            assert result["success"] is False
            assert result["artifacts_sent"] == 0
            assert "timed out" in result["message"].lower()

    def test_push_file_not_found(self, tmp_path):
        cli = FossilCLI(binary="/nonexistent/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=FileNotFoundError("No such file")):
            result = cli.push(repo_path)
            assert result["success"] is False
            assert result["artifacts_sent"] == 0


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


class TestSync:
    def test_sync_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="sync complete")):
            result = cli.sync(repo_path)
            assert result["success"] is True
            assert result["message"] == "sync complete"

    def test_sync_with_remote_url(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="ok")) as mock_run:
            cli.sync(repo_path, remote_url="https://fossil.example.com/repo")
            cmd = mock_run.call_args[0][0]
            assert "https://fossil.example.com/repo" in cmd

    def test_sync_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail(stdout="error")):
            result = cli.sync(repo_path)
            assert result["success"] is False

    def test_sync_timeout(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)):
            result = cli.sync(repo_path)
            assert result["success"] is False
            assert "timed out" in result["message"].lower()

    def test_sync_file_not_found(self, tmp_path):
        cli = FossilCLI(binary="/nonexistent/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=FileNotFoundError("No such file")):
            result = cli.sync(repo_path)
            assert result["success"] is False


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


class TestPull:
    def test_pull_success_with_artifacts(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="Round-trips: 1  received: 12")):
            result = cli.pull(repo_path)
            assert result["success"] is True
            assert result["artifacts_received"] == 12

    def test_pull_no_artifacts(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="nothing new")):
            result = cli.pull(repo_path)
            assert result["success"] is True
            assert result["artifacts_received"] == 0

    def test_pull_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail(stdout="connection refused")):
            result = cli.pull(repo_path)
            assert result["success"] is False

    def test_pull_timeout(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
            result = cli.pull(repo_path)
            assert result["success"] is False
            assert result["artifacts_received"] == 0

    def test_pull_file_not_found(self, tmp_path):
        cli = FossilCLI(binary="/nonexistent/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=FileNotFoundError("No such file")):
            result = cli.pull(repo_path)
            assert result["success"] is False
            assert result["artifacts_received"] == 0


# ---------------------------------------------------------------------------
# get_remote_url
# ---------------------------------------------------------------------------


class TestGetRemoteUrl:
    def test_returns_url_on_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        result_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="https://fossil.example.com/repo\n", stderr="")
        with patch("subprocess.run", return_value=result_proc):
            url = cli.get_remote_url(repo_path)
            assert url == "https://fossil.example.com/repo"

    def test_returns_empty_on_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        result_proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not configured")
        with patch("subprocess.run", return_value=result_proc):
            url = cli.get_remote_url(repo_path)
            assert url == ""

    def test_returns_empty_on_file_not_found(self, tmp_path):
        cli = FossilCLI(binary="/nonexistent/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=FileNotFoundError):
            url = cli.get_remote_url(repo_path)
            assert url == ""

    def test_returns_empty_on_timeout(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            url = cli.get_remote_url(repo_path)
            assert url == ""


# ---------------------------------------------------------------------------
# wiki_commit
# ---------------------------------------------------------------------------


class TestWikiCommit:
    def test_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            result = cli.wiki_commit(repo_path, "Home", "# Welcome")
            assert result is True
            assert mock_run.call_args[1]["input"] == "# Welcome"
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "wiki", "commit", "Home", "-R", str(repo_path)]

    def test_with_user(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            cli.wiki_commit(repo_path, "Home", "content", user="admin")
            cmd = mock_run.call_args[0][0]
            assert "--technote-user" in cmd
            assert "admin" in cmd

    def test_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.wiki_commit(repo_path, "Missing", "content")
            assert result is False


# ---------------------------------------------------------------------------
# wiki_create
# ---------------------------------------------------------------------------


class TestWikiCreate:
    def test_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            result = cli.wiki_create(repo_path, "NewPage", "# New content")
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "wiki", "create", "NewPage", "-R", str(repo_path)]
            assert mock_run.call_args[1]["input"] == "# New content"

    def test_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.wiki_create(repo_path, "Dup", "content")
            assert result is False


# ---------------------------------------------------------------------------
# ticket_add
# ---------------------------------------------------------------------------


class TestTicketAdd:
    def test_success_with_fields(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        fields = {"title": "Bug report", "status": "open", "type": "bug"}
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            result = cli.ticket_add(repo_path, fields)
            assert result is True
            cmd = mock_run.call_args[0][0]
            # Should have: fossil ticket add -R <path> title "Bug report" status open type bug
            assert cmd[:4] == ["/usr/bin/fossil", "ticket", "add", "-R"]
            assert "title" in cmd
            assert "Bug report" in cmd

    def test_empty_fields(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            result = cli.ticket_add(repo_path, {})
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "ticket", "add", "-R", str(repo_path)]

    def test_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.ticket_add(repo_path, {"title": "test"})
            assert result is False


# ---------------------------------------------------------------------------
# ticket_change
# ---------------------------------------------------------------------------


class TestTicketChange:
    def test_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        uuid = "abc123def456"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            result = cli.ticket_change(repo_path, uuid, {"status": "closed"})
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert cmd[:5] == ["/usr/bin/fossil", "ticket", "change", uuid, "-R"]
            assert "status" in cmd
            assert "closed" in cmd

    def test_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.ticket_change(repo_path, "badid", {"status": "open"})
            assert result is False


# ---------------------------------------------------------------------------
# technote_create
# ---------------------------------------------------------------------------


class TestTechnoteCreate:
    def test_with_explicit_timestamp(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            result = cli.technote_create(repo_path, "Release v1.0", "Details here", timestamp="2026-04-07T12:00:00")
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert "--technote" in cmd
            assert "2026-04-07T12:00:00" in cmd
            assert mock_run.call_args[1]["input"] == "Details here"

    def test_auto_generates_timestamp(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            cli.technote_create(repo_path, "Note", "body")
            cmd = mock_run.call_args[0][0]
            # Should have generated a timestamp in ISO format
            ts_idx = cmd.index("--technote") + 1
            assert "T" in cmd[ts_idx]  # ISO datetime has T separator

    def test_with_user(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            cli.technote_create(repo_path, "Note", "body", timestamp="2026-01-01T00:00:00", user="author")
            cmd = mock_run.call_args[0][0]
            assert "--technote-user" in cmd
            assert "author" in cmd

    def test_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.technote_create(repo_path, "Fail", "body", timestamp="2026-01-01T00:00:00")
            assert result is False


# ---------------------------------------------------------------------------
# technote_edit
# ---------------------------------------------------------------------------


class TestTechnoteEdit:
    def test_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            result = cli.technote_edit(repo_path, "abc123", "Updated body")
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert "--technote" in cmd
            assert "abc123" in cmd
            assert mock_run.call_args[1]["input"] == "Updated body"

    def test_with_user(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            cli.technote_edit(repo_path, "abc123", "body", user="editor")
            cmd = mock_run.call_args[0][0]
            assert "--technote-user" in cmd
            assert "editor" in cmd

    def test_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.technote_edit(repo_path, "badid", "body")
            assert result is False


# ---------------------------------------------------------------------------
# uv_add
# ---------------------------------------------------------------------------


class TestUvAdd:
    def test_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        filepath = tmp_path / "logo.png"
        with patch("subprocess.run", return_value=_ok()) as mock_run:
            result = cli.uv_add(repo_path, "logo.png", filepath)
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "uv", "add", str(filepath), "--as", "logo.png", "-R", str(repo_path)]

    def test_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.uv_add(repo_path, "file.txt", tmp_path / "file.txt")
            assert result is False


# ---------------------------------------------------------------------------
# uv_cat
# ---------------------------------------------------------------------------


class TestUvCat:
    def test_returns_bytes_on_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        content = b"\x89PNG\r\n\x1a\n"  # PNG header bytes
        with patch("subprocess.run", return_value=_ok_bytes(stdout=content)) as mock_run:
            result = cli.uv_cat(repo_path, "logo.png")
            assert result == content
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "uv", "cat", "logo.png", "-R", str(repo_path)]

    def test_raises_file_not_found_on_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with (
            patch("subprocess.run", return_value=_ok_bytes(returncode=1)),
            pytest.raises(FileNotFoundError, match="Unversioned file not found"),
        ):
            cli.uv_cat(repo_path, "missing.txt")


# ---------------------------------------------------------------------------
# git_export (supplements TestGitExportTokenHandling in test_security.py)
# ---------------------------------------------------------------------------


class TestGitExport:
    def test_basic_export_no_autopush(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        mirror_dir = tmp_path / "mirror"
        with patch("subprocess.run", return_value=_ok(stdout="exported 5 commits")) as mock_run:
            result = cli.git_export(repo_path, mirror_dir)
            assert result["success"] is True
            assert result["message"] == "exported 5 commits"
            cmd = mock_run.call_args[0][0]
            assert cmd == ["/usr/bin/fossil", "git", "export", str(mirror_dir), "-R", str(repo_path)]
            # mirror_dir should be created
            assert mirror_dir.exists()

    def test_with_autopush_url(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        mirror_dir = tmp_path / "mirror"
        with patch("subprocess.run", return_value=_ok(stdout="pushed")) as mock_run:
            cli.git_export(repo_path, mirror_dir, autopush_url="https://github.com/user/repo.git")
            cmd = mock_run.call_args[0][0]
            assert "--autopush" in cmd
            assert "https://github.com/user/repo.git" in cmd

    def test_timeout_returns_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        mirror_dir = tmp_path / "mirror"
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 300)):
            result = cli.git_export(repo_path, mirror_dir)
            assert result["success"] is False
            assert "timed out" in result["message"].lower()

    def test_failure_returncode(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        mirror_dir = tmp_path / "mirror"
        with patch("subprocess.run", return_value=_ok(stdout="fatal error", returncode=1)):
            result = cli.git_export(repo_path, mirror_dir)
            assert result["success"] is False

    def test_temp_files_cleaned_on_success(self, tmp_path):
        """Askpass and token temp files are removed after successful export."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        mirror_dir = tmp_path / "mirror"

        created_files = []

        original_mkstemp = __import__("tempfile").mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = original_mkstemp(**kwargs)
            created_files.append(path)
            return fd, path

        with (
            patch("subprocess.run", return_value=_ok(stdout="ok")),
            patch("tempfile.mkstemp", side_effect=tracking_mkstemp),
        ):
            cli.git_export(repo_path, mirror_dir, autopush_url="https://github.com/u/r.git", auth_token="tok123")

        # Both temp files should be cleaned up
        assert len(created_files) == 2
        for f in created_files:
            assert not os.path.exists(f)

    def test_temp_files_cleaned_on_timeout(self, tmp_path):
        """Askpass and token temp files are removed even when subprocess times out."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        mirror_dir = tmp_path / "mirror"

        created_files = []
        original_mkstemp = __import__("tempfile").mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = original_mkstemp(**kwargs)
            created_files.append(path)
            return fd, path

        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 300)),
            patch("tempfile.mkstemp", side_effect=tracking_mkstemp),
        ):
            cli.git_export(repo_path, mirror_dir, autopush_url="https://github.com/u/r.git", auth_token="tok123")

        for f in created_files:
            assert not os.path.exists(f)

    def test_no_redaction_when_no_token(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        mirror_dir = tmp_path / "mirror"
        with patch("subprocess.run", return_value=_ok(stdout="push ok")):
            result = cli.git_export(repo_path, mirror_dir, autopush_url="https://github.com/u/r.git")
            assert result["message"] == "push ok"
            assert "[REDACTED]" not in result["message"]

    def test_combines_stdout_and_stderr(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        mirror_dir = tmp_path / "mirror"
        with patch("subprocess.run", return_value=_ok(stdout="out\n", stderr="err")):
            result = cli.git_export(repo_path, mirror_dir)
            assert "out" in result["message"]
            assert "err" in result["message"]


# ---------------------------------------------------------------------------
# generate_ssh_key
# ---------------------------------------------------------------------------


class TestGenerateSSHKey:
    def test_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        key_path = tmp_path / "keys" / "id_ed25519"
        pub_key_content = "ssh-ed25519 AAAAC3Nza...== fossilrepo"
        fingerprint_output = "256 SHA256:abcdef123456 fossilrepo (ED25519)"

        # Create the public key file that generate_ssh_key will try to read
        key_path.parent.mkdir(parents=True, exist_ok=True)

        with patch("subprocess.run") as mock_run:
            # ssh-keygen creates the key, then we read pubkey, then fingerprint
            def side_effect(cmd, **kwargs):
                if "-t" in cmd:
                    # Write fake pub key file on "creation"
                    key_path.with_suffix(".pub").write_text(pub_key_content)
                    return _ok()
                elif "-lf" in cmd:
                    return _ok(stdout=fingerprint_output)
                return _ok()

            mock_run.side_effect = side_effect
            result = cli.generate_ssh_key(key_path, comment="fossilrepo")

            assert result["success"] is True
            assert result["public_key"] == pub_key_content
            assert result["fingerprint"] == "SHA256:abcdef123456"

    def test_creates_parent_dirs(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        key_path = tmp_path / "deep" / "nested" / "id_ed25519"
        with patch("subprocess.run", return_value=_fail()):
            cli.generate_ssh_key(key_path)
            # Parent dirs should exist even if ssh-keygen fails
            assert key_path.parent.exists()

    def test_failure_returns_error_dict(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        key_path = tmp_path / "id_ed25519"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.generate_ssh_key(key_path)
            assert result["success"] is False
            assert result["public_key"] == ""
            assert result["fingerprint"] == ""

    def test_exception_returns_error_dict(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        key_path = tmp_path / "id_ed25519"
        with patch("subprocess.run", side_effect=Exception("ssh-keygen not found")):
            result = cli.generate_ssh_key(key_path)
            assert result["success"] is False
            assert "ssh-keygen not found" in result["error"]

    def test_keygen_command_uses_ed25519(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        key_path = tmp_path / "id_ed25519"
        with patch("subprocess.run", return_value=_fail()) as mock_run:
            cli.generate_ssh_key(key_path, comment="test-key")
            cmd = mock_run.call_args[0][0]
            assert cmd == ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", "test-key"]

    def test_fingerprint_empty_on_keygen_lf_failure(self, tmp_path):
        """If ssh-keygen -lf fails, fingerprint should be empty but success still True."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        key_path = tmp_path / "id_ed25519"
        pub_key_content = "ssh-ed25519 AAAAC3Nza...== test"

        with patch("subprocess.run") as mock_run:

            def side_effect(cmd, **kwargs):
                if "-t" in cmd:
                    key_path.with_suffix(".pub").write_text(pub_key_content)
                    return _ok()
                elif "-lf" in cmd:
                    return _fail()
                return _ok()

            mock_run.side_effect = side_effect
            result = cli.generate_ssh_key(key_path)
            assert result["success"] is True
            assert result["public_key"] == pub_key_content
            assert result["fingerprint"] == ""


# ---------------------------------------------------------------------------
# http_proxy
# ---------------------------------------------------------------------------


class TestHttpProxy:
    def test_parses_crlf_response(self, tmp_path):
        """Standard HTTP response with \\r\\n\\r\\n separator."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        raw_response = b"HTTP/1.1 200 OK\r\nContent-Type: application/x-fossil\r\n\r\n\x00\x01\x02\x03"
        with patch("subprocess.run", return_value=_ok_bytes(stdout=raw_response)):
            body, content_type = cli.http_proxy(repo_path, b"request_body")
            assert body == b"\x00\x01\x02\x03"
            assert content_type == "application/x-fossil"

    def test_parses_lf_response(self, tmp_path):
        """Fallback: \\n\\n separator (no \\r)."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        raw_response = b"Content-Type: text/html\n\n<html>body</html>"
        with patch("subprocess.run", return_value=_ok_bytes(stdout=raw_response)):
            body, content_type = cli.http_proxy(repo_path, b"req")
            assert body == b"<html>body</html>"
            assert content_type == "text/html"

    def test_no_separator_returns_entire_body(self, tmp_path):
        """If no header/body separator, treat entire output as body."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        raw_response = b"raw binary data with no headers"
        with patch("subprocess.run", return_value=_ok_bytes(stdout=raw_response)):
            body, content_type = cli.http_proxy(repo_path, b"req")
            assert body == raw_response
            assert content_type == "application/x-fossil"

    def test_localauth_flag(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok_bytes(stdout=b"\r\n\r\n")) as mock_run:
            cli.http_proxy(repo_path, b"body", localauth=True)
            cmd = mock_run.call_args[0][0]
            assert "--localauth" in cmd

    def test_no_localauth_flag(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok_bytes(stdout=b"\r\n\r\n")) as mock_run:
            cli.http_proxy(repo_path, b"body", localauth=False)
            cmd = mock_run.call_args[0][0]
            assert "--localauth" not in cmd

    def test_builds_http_request_on_stdin(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        request_body = b"\x00\x01binary-data"
        with patch("subprocess.run", return_value=_ok_bytes(stdout=b"\r\n\r\n")) as mock_run:
            cli.http_proxy(repo_path, request_body, content_type="application/x-fossil")
            http_input = mock_run.call_args[1]["input"]
            # Should contain POST, Host, Content-Type, Content-Length headers + body
            assert b"POST /xfer HTTP/1.1\r\n" in http_input
            assert b"Host: localhost\r\n" in http_input
            assert b"Content-Type: application/x-fossil\r\n" in http_input
            assert f"Content-Length: {len(request_body)}".encode() in http_input
            assert http_input.endswith(request_body)

    def test_default_content_type(self, tmp_path):
        """When no content_type provided, defaults to application/x-fossil."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok_bytes(stdout=b"\r\n\r\n")) as mock_run:
            cli.http_proxy(repo_path, b"body")
            http_input = mock_run.call_args[1]["input"]
            assert b"Content-Type: application/x-fossil\r\n" in http_input

    def test_timeout_raises(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)),
            pytest.raises(subprocess.TimeoutExpired),
        ):
            cli.http_proxy(repo_path, b"body")

    def test_file_not_found_raises(self, tmp_path):
        cli = FossilCLI(binary="/nonexistent/fossil")
        repo_path = tmp_path / "repo.fossil"
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            pytest.raises(FileNotFoundError),
        ):
            cli.http_proxy(repo_path, b"body")

    def test_nonzero_returncode_does_not_raise(self, tmp_path):
        """Non-zero exit code logs a warning but does not raise."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        raw_response = b"Content-Type: application/x-fossil\r\n\r\nbody"
        with patch("subprocess.run", return_value=_ok_bytes(stdout=raw_response, returncode=1)):
            body, ct = cli.http_proxy(repo_path, b"req")
            assert body == b"body"

    def test_gateway_interface_stripped(self, tmp_path):
        """GATEWAY_INTERFACE must not be in the env passed to fossil http."""
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with (
            patch.dict(os.environ, {"GATEWAY_INTERFACE": "CGI/1.1"}),
            patch("subprocess.run", return_value=_ok_bytes(stdout=b"\r\n\r\n")) as mock_run,
        ):
            cli.http_proxy(repo_path, b"body")
            env = mock_run.call_args[1]["env"]
            assert "GATEWAY_INTERFACE" not in env


# ---------------------------------------------------------------------------
# shun / shun_list
# ---------------------------------------------------------------------------


class TestShun:
    def test_shun_success(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="Shunned")):
            result = cli.shun(repo_path, "abc123def456")
            assert result["success"] is True
            assert "Shunned" in result["message"]

    def test_shun_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail(stdout="", stderr="not found")):
            result = cli.shun(repo_path, "badid")
            assert result["success"] is False
            assert "not found" in result["message"]

    def test_shun_combines_stdout_stderr(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="out\n", stderr="warning")):
            result = cli.shun(repo_path, "abc123")
            assert "out" in result["message"]
            assert "warning" in result["message"]


class TestShunList:
    def test_returns_uuids(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="abc123\ndef456\nghi789\n")):
            result = cli.shun_list(repo_path)
            assert result == ["abc123", "def456", "ghi789"]

    def test_returns_empty_on_failure(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_fail()):
            result = cli.shun_list(repo_path)
            assert result == []

    def test_strips_whitespace_and_empty_lines(self, tmp_path):
        cli = FossilCLI(binary="/usr/bin/fossil")
        repo_path = tmp_path / "repo.fossil"
        with patch("subprocess.run", return_value=_ok(stdout="\n  abc123  \n\n  def456\n\n")):
            result = cli.shun_list(repo_path)
            assert result == ["abc123", "def456"]

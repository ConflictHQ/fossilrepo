"""Thin wrapper around the fossil binary for write operations."""

import subprocess
from pathlib import Path


class FossilCLI:
    """Wrapper around the fossil binary for write operations."""

    def __init__(self, binary: str | None = None):
        if binary is None:
            from constance import config

            binary = config.FOSSIL_BINARY_PATH
        self.binary = binary

    @property
    def _env(self):
        import os

        return {**os.environ, "USER": "fossilrepo"}

    def _run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        cmd = [self.binary, *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True, env=self._env)

    def ensure_default_user(self, repo_path: Path, username: str = "fossilrepo") -> None:
        """Ensure a default user exists in the repo. Creates if needed."""
        try:
            # Check if user exists
            result = subprocess.run(
                [self.binary, "user", "list", "-R", str(repo_path)],
                capture_output=True,
                text=True,
                timeout=10,
                env=self._env,
            )
            if username not in result.stdout:
                subprocess.run(
                    [self.binary, "user", "new", username, "", username, "-R", str(repo_path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=self._env,
                )
            subprocess.run(
                [self.binary, "user", "default", username, "-R", str(repo_path)],
                capture_output=True,
                text=True,
                timeout=10,
                env=self._env,
            )
        except Exception:
            pass

    def init(self, path: Path) -> Path:
        """Create a new .fossil repository."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run("init", str(path))
        return path

    def version(self) -> str:
        result = self._run("version")
        return result.stdout.strip()

    def is_available(self) -> bool:
        try:
            self._run("version")
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def render_pikchr(self, source: str) -> str:
        """Render Pikchr markup to SVG. Returns SVG string or empty on failure."""
        try:
            result = subprocess.run(
                [self.binary, "pikchr", "-"],
                input=source,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return ""

    def blame(self, repo_path: Path, filename: str) -> list[dict]:
        """Run fossil blame on a file. Returns [{user, uuid, line_num, text}].

        Requires creating a temp checkout since blame needs an open checkout.
        """
        import tempfile

        lines = []
        tmpdir = tempfile.mkdtemp(prefix="fossilrepo-blame-")
        try:
            # Open a checkout in the temp dir
            subprocess.run(
                [self.binary, "open", str(repo_path), "--workdir", tmpdir],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=tmpdir,
            )
            # Run blame
            result = subprocess.run(
                [self.binary, "blame", filename],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=tmpdir,
            )
            if result.returncode == 0:
                import re

                for line in result.stdout.splitlines():
                    # Format: "hash date user: code"
                    m = re.match(r"([0-9a-f]+)\s+(\S+)\s+([^:]+):\s?(.*)", line)
                    if m:
                        lines.append(
                            {
                                "uuid": m.group(1),
                                "date": m.group(2),
                                "user": m.group(3).strip(),
                                "text": m.group(4),
                            }
                        )
            # Close checkout
            subprocess.run([self.binary, "close", "--force"], capture_output=True, cwd=tmpdir, timeout=10, env=self._env)
        except Exception:
            pass
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)
        return lines

    def pull(self, repo_path: Path) -> dict:
        """Pull updates from the remote. Returns {success, artifacts_received, message}."""
        try:
            result = subprocess.run(
                [self.binary, "pull", "-R", str(repo_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            import re

            artifacts = 0
            m = re.search(r"received:\s*(\d+)", result.stdout)
            if m:
                artifacts = int(m.group(1))
            return {"success": result.returncode == 0, "artifacts_received": artifacts, "message": result.stdout.strip()}
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"success": False, "artifacts_received": 0, "message": str(e)}

    def get_remote_url(self, repo_path: Path) -> str:
        """Get the configured remote URL for a repo."""
        try:
            result = subprocess.run(
                [self.binary, "remote", "-R", str(repo_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""

    def wiki_commit(self, repo_path: Path, page_name: str, content: str, user: str = "") -> bool:
        """Create or update a wiki page. Pipes content to fossil wiki commit."""
        cmd = [self.binary, "wiki", "commit", page_name, "-R", str(repo_path)]
        if user:
            cmd.extend(["--technote-user", user])
        result = subprocess.run(cmd, input=content, capture_output=True, text=True, timeout=30, env=self._env)
        return result.returncode == 0

    def wiki_create(self, repo_path: Path, page_name: str, content: str) -> bool:
        """Create a new wiki page."""
        cmd = [self.binary, "wiki", "create", page_name, "-R", str(repo_path)]
        result = subprocess.run(cmd, input=content, capture_output=True, text=True, timeout=30, env=self._env)
        return result.returncode == 0

    def ticket_add(self, repo_path: Path, fields: dict) -> bool:
        """Add a new ticket. Fields dict maps field names to values."""
        cmd = [self.binary, "ticket", "add", "-R", str(repo_path)]
        for key, value in fields.items():
            cmd.append(f"{key}")
            cmd.append(f"{value}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=self._env)
        return result.returncode == 0

    def ticket_change(self, repo_path: Path, uuid: str, fields: dict) -> bool:
        """Update an existing ticket."""
        cmd = [self.binary, "ticket", "change", uuid, "-R", str(repo_path)]
        for key, value in fields.items():
            cmd.append(f"{key}")
            cmd.append(f"{value}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=self._env)
        return result.returncode == 0

    def git_export(self, repo_path: Path, mirror_dir: Path, autopush_url: str = "") -> dict:
        """Export Fossil repo to a Git mirror directory. Incremental.

        Returns {success, message}.
        """
        mirror_dir.mkdir(parents=True, exist_ok=True)
        cmd = [self.binary, "git", "export", str(mirror_dir), "-R", str(repo_path)]
        if autopush_url:
            cmd.extend(["--autopush", autopush_url])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=self._env)
            return {"success": result.returncode == 0, "message": (result.stdout + result.stderr).strip()}
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "Export timed out after 5 minutes"}

    def generate_ssh_key(self, key_path: Path, comment: str = "fossilrepo") -> dict:
        """Generate an SSH key pair for Git authentication.

        Returns {success, public_key, fingerprint}.
        """
        import os

        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", comment],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ},
            )
            if result.returncode == 0:
                pub_key = key_path.with_suffix(".pub").read_text().strip()
                # Get fingerprint
                fp_result = subprocess.run(
                    ["ssh-keygen", "-lf", str(key_path.with_suffix(".pub"))],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                fingerprint = fp_result.stdout.strip().split()[1] if fp_result.returncode == 0 else ""
                return {"success": True, "public_key": pub_key, "fingerprint": fingerprint}
        except Exception as e:
            return {"success": False, "public_key": "", "fingerprint": "", "error": str(e)}
        return {"success": False, "public_key": "", "fingerprint": ""}

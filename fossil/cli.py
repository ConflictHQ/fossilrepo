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

    def _run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        cmd = [self.binary, *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)

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

    def wiki_commit(self, repo_path: Path, page_name: str, content: str, user: str = "") -> bool:
        """Create or update a wiki page. Pipes content to fossil wiki commit."""
        cmd = [self.binary, "wiki", "commit", page_name, "-R", str(repo_path)]
        if user:
            cmd.extend(["--technote-user", user])
        result = subprocess.run(cmd, input=content, capture_output=True, text=True, timeout=30)
        return result.returncode == 0

    def wiki_create(self, repo_path: Path, page_name: str, content: str) -> bool:
        """Create a new wiki page."""
        cmd = [self.binary, "wiki", "create", page_name, "-R", str(repo_path)]
        result = subprocess.run(cmd, input=content, capture_output=True, text=True, timeout=30)
        return result.returncode == 0

    def ticket_add(self, repo_path: Path, fields: dict) -> bool:
        """Add a new ticket. Fields dict maps field names to values."""
        cmd = [self.binary, "ticket", "add", "-R", str(repo_path)]
        for key, value in fields.items():
            cmd.append(f"{key}")
            cmd.append(f"{value}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0

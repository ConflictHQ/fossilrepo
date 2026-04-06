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

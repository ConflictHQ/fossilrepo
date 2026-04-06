"""Fossil repository management — create, delete, list, inspect repos."""

from pathlib import Path

from fossilrepo.server.config import ServerConfig


class RepoInfo:
    """Information about a single Fossil repository."""

    def __init__(self, name: str, path: Path, size_bytes: int) -> None:
        self.name = name
        self.path = path
        self.size_bytes = size_bytes


class FossilRepoManager:
    """Manages Fossil repositories on the server.

    Handles repo lifecycle: creation via `fossil init`, deletion (soft — moves
    to trash), listing, and metadata inspection. Coordinates with Litestream
    for S3 replication of new repos.
    """

    def __init__(self, config: ServerConfig | None = None) -> None:
        self.config = config or ServerConfig()

    def create_repo(self, name: str) -> RepoInfo:
        """Create a new Fossil repository.

        Runs `fossil init` to create the .fossil file in the data directory,
        registers the repo with Caddy for subdomain routing, and ensures
        Litestream picks up the new file for replication.

        Args:
            name: Repository name. Used as the subdomain and filename.

        Returns:
            RepoInfo for the newly created repository.
        """
        raise NotImplementedError

    def delete_repo(self, name: str) -> None:
        """Soft-delete a Fossil repository.

        Moves the .fossil file to a trash directory rather than deleting it.
        Removes the Caddy subdomain route. Litestream retains the S3 replica.

        Args:
            name: Repository name to delete.
        """
        raise NotImplementedError

    def list_repos(self) -> list[RepoInfo]:
        """List all active Fossil repositories.

        Scans the data directory for .fossil files and returns metadata
        for each.

        Returns:
            List of RepoInfo objects for all active repositories.
        """
        raise NotImplementedError

    def get_repo_info(self, name: str) -> RepoInfo:
        """Get detailed information about a specific repository.

        Args:
            name: Repository name to inspect.

        Returns:
            RepoInfo with metadata about the repository.

        Raises:
            FileNotFoundError: If the repository does not exist.
        """
        raise NotImplementedError

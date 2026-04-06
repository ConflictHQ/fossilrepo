"""Fossil-to-Git mirror — sync commits, tickets, and wiki to GitHub/GitLab."""

from pathlib import Path

from fossilrepo.sync.mappings import CommitMapping, TicketMapping, WikiMapping


class FossilMirror:
    """Mirrors a Fossil repository to a Git remote (GitHub or GitLab).

    Fossil is the source of truth. The Git remote is a downstream mirror
    for ecosystem visibility. Syncs commits, optionally maps tickets to
    issues and wiki pages to docs.
    """

    def __init__(self, fossil_path: Path, remote_url: str) -> None:
        self.fossil_path = fossil_path
        self.remote_url = remote_url

    def sync_to_github(
        self,
        *,
        include_tickets: bool = False,
        include_wiki: bool = False,
    ) -> None:
        """Run a full sync to a GitHub repository.

        Exports Fossil commits to Git format and pushes to the GitHub remote.
        Optionally syncs tickets as GitHub Issues and wiki as repo docs.

        Args:
            include_tickets: If True, map Fossil tickets to GitHub Issues.
            include_wiki: If True, export Fossil wiki pages to repo docs.
        """
        raise NotImplementedError

    def sync_to_gitlab(
        self,
        *,
        include_tickets: bool = False,
        include_wiki: bool = False,
    ) -> None:
        """Run a full sync to a GitLab repository.

        Exports Fossil commits to Git format and pushes to the GitLab remote.
        Optionally syncs tickets as GitLab Issues and wiki pages.

        Args:
            include_tickets: If True, map Fossil tickets to GitLab Issues.
            include_wiki: If True, export Fossil wiki pages to GitLab wiki.
        """
        raise NotImplementedError

    def sync_commits(self) -> list[CommitMapping]:
        """Sync Fossil commits to the Git remote.

        Exports the Fossil timeline as Git commits and pushes to the
        configured remote. Returns a mapping of Fossil checkin hashes
        to Git commit SHAs.

        Returns:
            List of CommitMapping objects for each synced commit.
        """
        raise NotImplementedError

    def sync_tickets(self) -> list[TicketMapping]:
        """Sync Fossil tickets to the remote issue tracker.

        Maps Fossil ticket fields to GitHub/GitLab issue fields. Creates
        new issues for new tickets, updates existing ones.

        Returns:
            List of TicketMapping objects for each synced ticket.
        """
        raise NotImplementedError

    def sync_wiki(self) -> list[WikiMapping]:
        """Sync Fossil wiki pages to the remote.

        Exports Fossil wiki pages as Markdown files. For GitHub, these go
        into a docs/ directory. For GitLab, they go to the project wiki.

        Returns:
            List of WikiMapping objects for each synced page.
        """
        raise NotImplementedError

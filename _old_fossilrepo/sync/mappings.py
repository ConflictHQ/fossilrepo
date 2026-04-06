"""Data models for Fossil-to-Git sync mappings."""

from datetime import datetime

from pydantic import BaseModel, Field


class CommitMapping(BaseModel):
    """Maps a Fossil checkin to a Git commit."""

    fossil_hash: str = Field(description="Fossil checkin hash (SHA1).")
    git_sha: str = Field(description="Corresponding Git commit SHA.")
    timestamp: datetime = Field(description="Commit timestamp.")
    message: str = Field(description="Commit message.")
    author: str = Field(description="Author name.")


class TicketMapping(BaseModel):
    """Maps a Fossil ticket to a GitHub/GitLab issue."""

    fossil_ticket_id: str = Field(description="Fossil ticket UUID.")
    remote_issue_number: int = Field(description="GitHub/GitLab issue number.")
    remote_issue_url: str = Field(description="URL to the remote issue.")
    title: str = Field(description="Ticket/issue title.")
    status: str = Field(description="Current status (open, closed, etc.).")
    last_synced: datetime = Field(description="Timestamp of last sync.")


class WikiMapping(BaseModel):
    """Maps a Fossil wiki page to a remote doc/wiki page."""

    fossil_page_name: str = Field(description="Fossil wiki page name.")
    remote_path: str = Field(
        description="Path in the remote repo (e.g., docs/page.md) or wiki URL."
    )
    last_synced: datetime = Field(description="Timestamp of last sync.")
    content_hash: str = Field(
        description="Hash of the content at last sync, for change detection."
    )

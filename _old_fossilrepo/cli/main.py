"""fossilrepo CLI — manage Fossil servers, repos, and Git sync."""

import click
from rich.console import Console

console = Console()


@click.group()
@click.version_option(package_name="fossilrepo")
def cli() -> None:
    """fossilrepo — self-hosted Fossil SCM infrastructure."""


# ---------------------------------------------------------------------------
# Server commands
# ---------------------------------------------------------------------------


@cli.group()
def server() -> None:
    """Manage the Fossil server."""


@server.command()
def start() -> None:
    """Start the Fossil server (Docker + Caddy + Litestream)."""
    console.print("[bold]Starting Fossil server...[/bold]")
    raise NotImplementedError


@server.command()
def stop() -> None:
    """Stop the Fossil server."""
    console.print("[bold]Stopping Fossil server...[/bold]")
    raise NotImplementedError


@server.command()
def status() -> None:
    """Show Fossil server status."""
    console.print("[bold]Server status:[/bold]")
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Repo commands
# ---------------------------------------------------------------------------


@cli.group()
def repo() -> None:
    """Manage Fossil repositories."""


@repo.command()
@click.argument("name")
def create(name: str) -> None:
    """Create a new Fossil repository."""
    console.print(f"[bold]Creating repo:[/bold] {name}")
    raise NotImplementedError


@repo.command(name="list")
def list_repos() -> None:
    """List all Fossil repositories."""
    raise NotImplementedError


@repo.command()
@click.argument("name")
def delete(name: str) -> None:
    """Delete a Fossil repository."""
    console.print(f"[bold]Deleting repo:[/bold] {name}")
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Sync commands
# ---------------------------------------------------------------------------


@cli.group()
def sync() -> None:
    """Sync Fossil repos to GitHub/GitLab."""


@sync.command()
@click.argument("repo_name")
@click.option("--remote", required=True, help="Git remote URL to sync to.")
@click.option("--tickets/--no-tickets", default=False, help="Sync tickets as issues.")
@click.option("--wiki/--no-wiki", default=False, help="Sync wiki pages.")
def run(repo_name: str, remote: str, tickets: bool, wiki: bool) -> None:
    """Run a sync from a Fossil repo to a Git remote."""
    console.print(f"[bold]Syncing[/bold] {repo_name} -> {remote}")
    raise NotImplementedError


@sync.command()
@click.argument("repo_name")
def status(repo_name: str) -> None:  # noqa: F811
    """Show sync status for a repository."""
    console.print(f"[bold]Sync status for:[/bold] {repo_name}")
    raise NotImplementedError

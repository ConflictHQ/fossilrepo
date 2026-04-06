"""fossilrepo-ctl — operator CLI for the fossilrepo omnibus stack.

Similar to gitlab-ctl: manages the full stack (Django, Fossil, Caddy,
Litestream, Celery, Postgres, Redis) as a single unit.
"""

import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yaml"
FOSSIL_COMPOSE_FILE = PROJECT_ROOT / "docker" / "docker-compose.fossil.yml"


def _compose(*args: str, fossil: bool = False) -> subprocess.CompletedProcess:
    """Run a docker compose command."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    if fossil:
        cmd.extend(["-f", str(FOSSIL_COMPOSE_FILE)])
    cmd.extend(args)
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT))


@click.group()
@click.version_option(version="0.1.0")
def cli() -> None:
    """fossilrepo-ctl — manage the fossilrepo omnibus stack."""


# ---------------------------------------------------------------------------
# Stack commands (like gitlab-ctl start/stop/restart/status)
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--detach/--no-detach", "-d", default=True, help="Run in background.")
def start(detach: bool) -> None:
    """Start the full fossilrepo stack."""
    console.print("[bold green]Starting fossilrepo stack...[/bold green]")
    args = ["up"]
    if detach:
        args.append("-d")
    _compose(*args)


@cli.command()
def stop() -> None:
    """Stop the full fossilrepo stack."""
    console.print("[bold yellow]Stopping fossilrepo stack...[/bold yellow]")
    _compose("down")


@cli.command()
def restart() -> None:
    """Restart the full fossilrepo stack."""
    console.print("[bold yellow]Restarting fossilrepo stack...[/bold yellow]")
    _compose("restart")


@cli.command()
def status() -> None:
    """Show status of all fossilrepo services."""
    _compose("ps")


@cli.command()
@click.argument("service", required=False)
@click.option("--follow/--no-follow", "-f", default=True, help="Follow log output.")
@click.option("--tail", default="100", help="Number of lines to show.")
def logs(service: str | None, follow: bool, tail: str) -> None:
    """Tail logs from fossilrepo services."""
    args = ["logs", "--tail", tail]
    if follow:
        args.append("-f")
    if service:
        args.append(service)
    _compose(*args)


# ---------------------------------------------------------------------------
# Setup / reconfigure (like gitlab-ctl reconfigure)
# ---------------------------------------------------------------------------


@cli.command()
def reconfigure() -> None:
    """Rebuild and reconfigure the stack (migrations, static files, etc.)."""
    console.print("[bold]Reconfiguring fossilrepo...[/bold]")
    _compose("build")
    _compose("up", "-d")
    console.print("[bold]Running migrations...[/bold]")
    _compose("exec", "backend", "python", "manage.py", "migrate")
    console.print("[bold]Collecting static files...[/bold]")
    _compose("exec", "backend", "python", "manage.py", "collectstatic", "--noinput")
    console.print("[bold green]Reconfiguration complete.[/bold green]")


@cli.command()
def seed() -> None:
    """Load seed data (dev users, sample data)."""
    _compose("exec", "backend", "python", "manage.py", "seed")


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
    raise NotImplementedError("Repo creation not yet implemented")


@repo.command(name="list")
def list_repos() -> None:
    """List all Fossil repositories."""
    raise NotImplementedError("Repo listing not yet implemented")


@repo.command()
@click.argument("name")
def delete(name: str) -> None:
    """Delete a Fossil repository (soft delete)."""
    console.print(f"[bold]Deleting repo:[/bold] {name}")
    raise NotImplementedError("Repo deletion not yet implemented")


# ---------------------------------------------------------------------------
# Sync commands
# ---------------------------------------------------------------------------


@cli.group()
def sync() -> None:
    """Sync Fossil repos to GitHub/GitLab."""


@sync.command(name="run")
@click.argument("repo_name")
@click.option("--remote", required=True, help="Git remote URL.")
@click.option("--tickets/--no-tickets", default=False, help="Sync tickets as issues.")
@click.option("--wiki/--no-wiki", default=False, help="Sync wiki pages.")
def sync_run(repo_name: str, remote: str, tickets: bool, wiki: bool) -> None:
    """Run a sync from a Fossil repo to a Git remote."""
    console.print(f"[bold]Syncing[/bold] {repo_name} -> {remote}")
    raise NotImplementedError("Sync not yet implemented")


@sync.command(name="status")
@click.argument("repo_name")
def sync_status(repo_name: str) -> None:
    """Show sync status for a repository."""
    console.print(f"[bold]Sync status for:[/bold] {repo_name}")
    raise NotImplementedError("Sync status not yet implemented")


# ---------------------------------------------------------------------------
# Backup commands
# ---------------------------------------------------------------------------


@cli.group()
def backup() -> None:
    """Backup and restore operations."""


@backup.command(name="create")
def backup_create() -> None:
    """Create a backup of all repos and database."""
    console.print("[bold]Creating backup...[/bold]")
    raise NotImplementedError("Backup not yet implemented")


@backup.command(name="restore")
@click.argument("path")
def backup_restore(path: str) -> None:
    """Restore from a backup."""
    console.print(f"[bold]Restoring from:[/bold] {path}")
    raise NotImplementedError("Restore not yet implemented")

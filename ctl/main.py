"""fossilrepo-ctl — operator CLI for the fossilrepo omnibus stack.

Similar to gitlab-ctl: manages the full stack (Django, Fossil, Caddy,
Litestream, Celery, Postgres, Redis) as a single unit.
"""

import subprocess
from pathlib import Path

import click
from rich.console import Console

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
    import django

    django.setup()

    from fossil.cli import FossilCLI
    from fossil.models import FossilRepository
    from organization.models import Organization
    from projects.models import Project

    console.print(f"[bold]Creating repo:[/bold] {name}")
    cli = FossilCLI()
    if not cli.is_available():
        console.print("[red]Fossil binary not found.[/red]")
        return

    org = Organization.objects.first()
    if not org:
        console.print("[red]No organization found. Run seed first.[/red]")
        return

    project, created = Project.objects.get_or_create(name=name, defaults={"organization": org, "visibility": "private"})
    if created:
        console.print(f"  Created project: [cyan]{project.slug}[/cyan]")

    fossil_repo = FossilRepository.objects.filter(project=project).first()
    if fossil_repo and fossil_repo.exists_on_disk:
        console.print(f"  Repo already exists: [cyan]{fossil_repo.full_path}[/cyan]")
    elif fossil_repo:
        cli.init(fossil_repo.full_path)
        fossil_repo.file_size_bytes = fossil_repo.full_path.stat().st_size
        fossil_repo.save(update_fields=["file_size_bytes", "updated_at", "version"])
        console.print(f"  Initialized: [green]{fossil_repo.full_path}[/green]")
    console.print("[bold green]Done.[/bold green]")


@repo.command(name="list")
def list_repos() -> None:
    """List all Fossil repositories."""
    import django

    django.setup()
    from rich.table import Table

    from fossil.models import FossilRepository

    repos = FossilRepository.objects.all()
    table = Table(title="Fossil Repositories")
    table.add_column("Project", style="cyan")
    table.add_column("Filename")
    table.add_column("Size", justify="right")
    table.add_column("On Disk", justify="center")
    for r in repos:
        size = f"{r.file_size_bytes / 1024:.0f} KB" if r.file_size_bytes else "—"
        table.add_row(r.project.name, r.filename, size, "yes" if r.exists_on_disk else "no")
    console.print(table)


@repo.command()
@click.argument("name")
def delete(name: str) -> None:
    """Delete a Fossil repository (soft delete)."""
    import django

    django.setup()
    from fossil.models import FossilRepository

    console.print(f"[bold]Deleting repo:[/bold] {name}")
    repo = FossilRepository.objects.filter(filename=f"{name}.fossil").first()
    if not repo:
        console.print(f"[red]Repo not found: {name}.fossil[/red]")
        return
    repo.soft_delete()
    console.print(f"  Soft-deleted: [yellow]{repo.filename}[/yellow]")
    console.print("[bold green]Done.[/bold green]")


# ---------------------------------------------------------------------------
# Sync commands
# ---------------------------------------------------------------------------


@cli.group()
def sync() -> None:
    """Sync Fossil repos to GitHub/GitLab."""


@sync.command(name="run")
@click.argument("repo_name")
@click.option("--mirror-id", type=int, help="Specific Git mirror ID to sync.")
def sync_run(repo_name: str, mirror_id: int | None = None) -> None:
    """Run Git sync for a Fossil repository."""
    import django

    django.setup()
    from fossil.models import FossilRepository
    from fossil.sync_models import GitMirror
    from fossil.tasks import run_git_sync

    repo = FossilRepository.objects.filter(filename=f"{repo_name}.fossil").first()
    if not repo:
        console.print(f"[red]Repo not found: {repo_name}.fossil[/red]")
        return

    mirrors = GitMirror.objects.filter(repository=repo, deleted_at__isnull=True).exclude(sync_mode="disabled")
    if mirror_id:
        mirrors = mirrors.filter(pk=mirror_id)

    if not mirrors.exists():
        console.print("[yellow]No Git mirrors configured for this repo.[/yellow]")
        return

    for mirror in mirrors:
        console.print(f"[bold]Syncing[/bold] {repo.filename} → {mirror.git_remote_url}")
        run_git_sync(mirror.pk)
        mirror.refresh_from_db()
        if mirror.last_sync_status == "success":
            console.print(f"  [green]Success[/green] — {mirror.last_sync_message[:100]}")
        else:
            console.print(f"  [red]Failed[/red] — {mirror.last_sync_message[:100]}")


@sync.command(name="status")
@click.argument("repo_name", required=False)
def sync_status(repo_name: str | None = None) -> None:
    """Show sync status for repositories."""
    import django

    django.setup()
    from rich.table import Table

    from fossil.sync_models import GitMirror

    mirrors = GitMirror.objects.filter(deleted_at__isnull=True)
    if repo_name:
        mirrors = mirrors.filter(repository__filename=f"{repo_name}.fossil")

    table = Table(title="Git Mirror Status")
    table.add_column("Repo", style="cyan")
    table.add_column("Remote")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("Last Sync")
    table.add_column("Syncs", justify="right")
    for m in mirrors:
        status_style = "green" if m.last_sync_status == "success" else "red" if m.last_sync_status == "failed" else "yellow"
        table.add_row(
            m.repository.filename,
            m.git_remote_url[:40],
            m.get_sync_mode_display(),
            f"[{status_style}]{m.last_sync_status or 'never'}[/{status_style}]",
            str(m.last_sync_at.strftime("%Y-%m-%d %H:%M") if m.last_sync_at else "—"),
            str(m.total_syncs),
        )
    console.print(table)


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

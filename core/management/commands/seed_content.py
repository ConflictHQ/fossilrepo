"""seed_content — idempotent seed for initial Knowledge Base pages and fossilrepo.io project.

Creates:
  - Default Organization (if none exists)
  - Knowledge Base page
  - FossilSCM Guide page
  - fossilrepo project (cloned from fossilrepo.io, if FOSSIL_DATA_DIR is writable)

Safe to run multiple times — all operations are idempotent.
"""

import os
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils.text import slugify


KNOWLEDGE_BASE_CONTENT = """\
# Fossilrepo Knowledge Base

**Self-hosted Fossil forge. One command, full-stack code hosting.**

Fossilrepo is an omnibus-style installer for a production Fossil SCM server. It packages
Fossil, Caddy (SSL/routing), Litestream (S3 backups), and a Django management layer into
a single deployable unit.

## Why Fossil?

A Fossil repository is a single SQLite file containing the full VCS history, issue tracker,
wiki, forum, and timeline. No external services. No rate limits. Portable — hand the file
to someone and they have everything.

- **Single-file repos** — each `.fossil` file is the entire project
- **Built-in everything** — issues, wiki, forum, timeline, web UI
- **No API rate limits** — ideal for CI agents and automation
- **Litestream replication** — continuous backup to S3 for free

## What You Get

| Component | Role |
|---|---|
| **Fossil server** | Serves all repos from a single process |
| **Caddy** | SSL termination, subdomain-per-repo routing |
| **Litestream** | Continuous SQLite replication to S3/MinIO |
| **Django management UI** | Repository lifecycle, user management, dashboards |
| **Sync bridge** | Bidirectional sync between Fossil and GitHub/GitLab |
| **Celery workers** | Background sync, scheduled tasks |

## Quick Start

```bash
fossil clone https://fossilrepo.io/projects/fossilrepo/ fossilrepo.fossil
fossil open fossilrepo.fossil --workdir fossilrepo
cd fossilrepo
make build
make seed
open http://localhost:8000
```

## Architecture

```
Caddy  (SSL termination, routing, subdomain per repo)
  +-- fossil server --repolist /data/repos/
        +-- /data/repos/
              |-- projecta.fossil
              |-- projectb.fossil
              +-- ...

Litestream -> S3/MinIO  (continuous replication, point-in-time recovery)
```

New project = `fossil init`. No restart, no config change. Litestream picks it up automatically.

## Resources

- [Creating Your First Repository](/wiki/FossilSCM-Guide)
- Source: https://fossilrepo.io/projects/fossilrepo/
"""

FOSSIL_SCM_GUIDE_CONTENT = """\
# FossilSCM Guide

## Creating Your First Repository

Once fossilrepo is running, you can create your first Fossil repository.

### Via the Dashboard

1. Log in at your fossilrepo URL
2. Navigate to **Repositories** in the sidebar
3. Click **Create Repository**
4. Enter a name (e.g., `my-project`)
5. Click **Create**

The repository is immediately available through the Fossil server.

### Via the CLI

```bash
# Inside the fossilrepo container
docker compose exec django python manage.py fossil_create my-project
```

This runs `fossil init`, registers the repo in the database, and (in production)
Caddy automatically routes the subdomain.

## Accessing Your Repository

### Web UI

Fossil includes a built-in web interface with:

- **Timeline** — commit history with diffs
- **Tickets** — issue tracker
- **Wiki** — project documentation
- **Forum** — discussions

### Clone via Fossil

```bash
fossil clone https://my-project.your-domain.com my-project.fossil
fossil open my-project.fossil
```

### Clone via Git (Mirror)

If you've configured the sync bridge:

```bash
git clone https://github.com/your-org/my-project.git
```

> **Note:** Git mirrors are downstream copies. Push changes to the Fossil repo —
> they'll sync to Git automatically.

## Key Fossil Commands

```bash
# Check status
fossil status

# Commit changes
fossil commit -m "my commit message"

# View timeline
fossil timeline

# Open a ticket
fossil ticket add title "Bug: ..." status Open type Code_Defect

# Create a wiki page
fossil wiki create "PageName" < page-content.md

# Push to remote
fossil push

# Pull from remote
fossil pull
```

## Fossil vs Git Concepts

| Git | Fossil |
|---|---|
| `commit` | `commit` (or `ci`) |
| `push` | `push` |
| `pull` | `pull` |
| `clone` | `clone` |
| `branch` | `branch new <name>` |
| `.git/` directory | Single `.fossil` file |
| GitHub Issues | Built-in ticket tracker |
| GitHub Wiki | Built-in wiki |

## Next Steps

- Configure the sync bridge to mirror to GitHub/GitLab
- Set up Litestream backups to S3
- Explore the MCP server for AI assistant integration
- Review the [Knowledge Base](/wiki/Knowledge-Base) for architecture details
"""


class Command(BaseCommand):
    help = "Seed initial content (Knowledge Base pages, fossilrepo.io project). Idempotent."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-clone",
            action="store_true",
            help="Skip cloning fossilrepo.io (default: clone if FOSSIL_DATA_DIR is writable)",
        )

    def handle(self, *args, **options):
        from organization.models import Organization

        # Get or create the default organization.
        org, created = Organization.objects.get_or_create(
            slug="default",
            defaults={"name": "Default"},
        )
        if created:
            self.stdout.write(self.style.SUCCESS("Created default organization"))
        else:
            self.stdout.write("Default organization already exists")

        # Seed wiki pages.
        self._seed_page(org, "Knowledge Base", KNOWLEDGE_BASE_CONTENT)
        self._seed_page(org, "FossilSCM Guide", FOSSIL_SCM_GUIDE_CONTENT)

        # Optionally clone fossilrepo.io and register it as a project.
        skip_clone = options["skip_clone"] or os.environ.get("SEED_SKIP_CLONE", "").lower() in ("1", "true", "yes")
        if not skip_clone:
            self._seed_fossilrepo_project(org)

    def _seed_page(self, org, name, content):
        from pages.models import Page

        slug = slugify(name)
        if Page.all_objects.filter(slug=slug).exists():
            self.stdout.write(f"Page already exists: {name}")
            return
        Page.objects.create(organization=org, name=name, content=content, is_published=True)
        self.stdout.write(self.style.SUCCESS(f"Created page: {name}"))

    def _seed_fossilrepo_project(self, org):
        from constance import config

        from fossil.models import FossilRepository
        from projects.models import Project

        data_dir = Path(config.FOSSIL_DATA_DIR)
        fossil_filename = "fossilrepo.fossil"
        fossil_path = data_dir / fossil_filename

        # Ensure DB record exists if file is already there.
        if fossil_path.exists():
            self.stdout.write(f"fossilrepo.fossil already on disk, ensuring DB record ...")
            self._ensure_project_record(org, fossil_path, fossil_filename)
            return

        # Try to clone.
        self.stdout.write("Cloning fossilrepo from fossilrepo.io ...")
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["fossil", "clone", "https://fossilrepo.io/projects/fossilrepo/", str(fossil_path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                self.stdout.write(self.style.SUCCESS("Cloned fossilrepo.io successfully"))
                self._ensure_project_record(org, fossil_path, fossil_filename)
            else:
                self.stdout.write(
                    self.style.WARNING(f"Clone failed (returncode={result.returncode}): {result.stderr[:300]}")
                )
        except subprocess.TimeoutExpired:
            self.stdout.write(self.style.WARNING("Clone timed out after 120s — skipping"))
        except FileNotFoundError:
            self.stdout.write(self.style.WARNING("fossil binary not found — skipping clone"))
        except OSError as e:
            self.stdout.write(self.style.WARNING(f"Clone skipped: {e}"))

    def _ensure_project_record(self, org, fossil_path, fossil_filename):
        from fossil.models import FossilRepository
        from projects.models import Project

        if Project.all_objects.filter(slug="fossilrepo").exists():
            self.stdout.write("Fossilrepo project already registered")
            return

        file_size = fossil_path.stat().st_size if fossil_path.exists() else 0

        project = Project.objects.create(
            organization=org,
            name="Fossilrepo",
            description="Self-hosted Fossil forge. One command, full-stack code hosting.",
            visibility=Project.Visibility.PUBLIC,
        )
        FossilRepository.objects.create(
            project=project,
            filename=fossil_filename,
            file_size_bytes=file_size,
            remote_url="https://fossilrepo.io/projects/fossilrepo/",
        )
        self.stdout.write(self.style.SUCCESS("Registered fossilrepo project in database"))

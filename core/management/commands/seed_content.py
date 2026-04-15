"""seed_content — idempotent seed for initial Knowledge Base pages and fossilrepo.io project.

Creates:
  - Default Organization (if none exists)
  - Knowledge Base landing page
  - FossilRepo Docs section (8 pages)
  - FossilSCM Guide page
  - fossilrepo project (.fossil seed from local copy, then fossilrepo.io fallback)

Seed resolution order for fossilrepo.fossil:
  1. FOSSILREPO_SEED_PATH env var  — explicit override, e.g. a pre-baked artifact
  2. /app/fossilrepo.fossil         — bundled by COPY . . in Dockerfile.ecr
  3. fossil clone fossilrepo.io     — remote fallback (requires network + fossil binary)

Safe to run multiple times — all operations are idempotent.
"""

import os
import shutil
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils.text import slugify


# ---------------------------------------------------------------------------
# Knowledge Base — landing only (user articles live here)
# ---------------------------------------------------------------------------

KNOWLEDGE_BASE_CONTENT = """\
# Knowledge Base

This is your organization's knowledge base. Create pages to document processes,
decisions, runbooks, and anything else your team needs to reference.

Use the **New Page** button to get started.
"""

# ---------------------------------------------------------------------------
# FossilRepo Docs
# ---------------------------------------------------------------------------

GETTING_STARTED_CONTENT = """\
# Getting Started

## Prerequisites

- Docker and Docker Compose
- `fossil` CLI (for cloning; `brew install fossil` or package manager)
- Git (optional, for the sync bridge)

## Quick Start

```bash
# Clone the fossilrepo repository
fossil clone https://fossilrepo.io/projects/fossilrepo/ fossilrepo.fossil
fossil open fossilrepo.fossil --workdir fossilrepo
cd fossilrepo

# Configure your environment
cp .env.example .env
# Edit .env — set SECRET_KEY, DATABASE_URL, FOSSIL_DATA_DIR at minimum

# Build and start
./fossilrepo-ctl reconfigure
./fossilrepo-ctl start

# Open the management UI
open http://localhost:8000
```

The default admin credentials are printed to the console on first run.

## What Happens at Startup

1. Django runs migrations
2. `seed_roles` creates the four predefined roles (Admin, Manager, Developer, Viewer)
3. `seed_content` creates KB landing and documentation pages
4. Fossil server starts serving all `.fossil` files from `FOSSIL_DATA_DIR`
5. Caddy comes up and routes subdomains

## Next Steps

- [Architecture Overview](/wiki/Architecture-Overview) — understand the stack
- [Setup Guide](/wiki/Setup-Guide) — full configuration reference
- [Agentic Development](/wiki/Agentic-Development) — using fossilrepo with AI agents
"""

ARCHITECTURE_OVERVIEW_CONTENT = """\
# Architecture Overview

## Stack

| Component | Technology |
|---|---|
| Backend | Django 5 + HTMX |
| Database | PostgreSQL 16 |
| SCM | Fossil |
| Proxy | Caddy |
| Backups | Litestream → S3 |
| Jobs | Celery + Redis |

## How It Works

Each Fossil repository is a single `.fossil` SQLite file. Caddy routes subdomain
requests to the Fossil server. Django provides the management UI. Litestream
continuously replicates repo files to S3.

See [Architecture](/wiki/Architecture) for the full technical breakdown.
"""

ARCHITECTURE_CONTENT = """\
# Architecture

## Fossil Access Layer

Fossilrepo uses two strategies to read Fossil data, chosen by operation:

| Strategy | Class | Used For |
|---|---|---|
| Direct SQLite | `FossilReader` | Read-heavy: timeline, tickets, wiki, tags |
| Fossil CLI subprocess | `FossilCLI` | Write operations: commit, push, branch create |
| HTTP proxy | `FossilProxy` | Pass-through: web UI, clone, checkout |
| SSH proxy | `FossilSSH` | Git/Fossil SSH clone and push |

`FossilReader` opens the `.fossil` file directly with SQLite — no Fossil process,
no lock contention, fast parallel reads. Writes go through `FossilCLI` subprocess
calls so Fossil maintains its internal consistency guarantees.

## Dual Database Model

Each project has two persistent stores:

- **PostgreSQL** — Django models: users, projects, tickets metadata, audit log,
  Celery results, notification preferences
- **`.fossil` file** — Full VCS history, wiki, tickets payload, forum, timeline

These are kept in sync by Celery background tasks. PostgreSQL is the source of
truth for access control; the `.fossil` file is the source of truth for SCM data.

## Permission Model

```
Organization
  └── Members (User → OrgRole)
        └── Teams (Group of Members)
              └── Projects (Team → ProjectRole: read/write/admin)
```

Permissions are Django Group-based. Each role (Admin, Manager, Developer, Viewer,
or custom) maps to a Django Group. Assigning a role adds the user to that group
and removes them from the previous one.

## Django Apps

| App | Responsibility |
|---|---|
| `organization` | Orgs, members, roles, teams |
| `projects` | Projects, project groups, settings |
| `fossil` | FossilRepository model, FossilReader/CLI/Proxy |
| `pages` | Knowledge Base (wiki-style pages) |
| `tickets` | Issue tracker (mirrors Fossil tickets) |
| `releases` | Releases / tags |
| `sync` | GitHub/GitLab sync bridge |
| `api` | REST API + MCP server |
| `notifications` | Notification preferences and delivery |

## Celery Task Schedule

| Task | Schedule | Purpose |
|---|---|---|
| `sync_all_repos` | Every 15 min | Pull from configured upstreams |
| `update_file_sizes` | Hourly | Refresh `.fossil` file size stats |
| `prune_old_results` | Daily | Clean up Celery result backend |
| `send_digest_notifications` | Daily / Weekly | Deliver digest emails |

## Caddy Routing

Caddy handles SSL termination and subdomain-per-repo routing:

```
https://my-project.your-domain.com  →  fossil server HTTP endpoint
https://your-domain.com             →  Django management UI (port 8000)
```

No wildcard certificate needed in development — Caddy uses its own CA.
"""

API_REFERENCE_CONTENT = """\
# API Reference

Base path: `/api/v1/`

Authentication: `Authorization: Bearer <token>` — tokens created in the UI at
**Project Settings → Tokens** (project-scoped) or **Admin → API Tokens** (org-scoped).

## Projects

| Method | Path | Description |
|---|---|---|
| `GET` | `/projects/` | List all accessible projects |
| `POST` | `/projects/` | Create a project |
| `GET` | `/projects/{slug}/` | Project detail |
| `PATCH` | `/projects/{slug}/` | Update project metadata |
| `DELETE` | `/projects/{slug}/` | Delete project |

## Timeline

| Method | Path | Description |
|---|---|---|
| `GET` | `/projects/{slug}/timeline/` | Commit history (paginated) |
| `GET` | `/projects/{slug}/timeline/{hash}/` | Single commit detail + diff |

## Tickets

| Method | Path | Description |
|---|---|---|
| `GET` | `/projects/{slug}/tickets/` | List tickets |
| `POST` | `/projects/{slug}/tickets/` | Create ticket |
| `GET` | `/projects/{slug}/tickets/{id}/` | Ticket detail |
| `PATCH` | `/projects/{slug}/tickets/{id}/` | Update ticket |
| `POST` | `/projects/{slug}/tickets/{id}/comments/` | Add comment |

## Wiki

| Method | Path | Description |
|---|---|---|
| `GET` | `/projects/{slug}/wiki/` | List wiki pages |
| `GET` | `/projects/{slug}/wiki/{page}/` | Wiki page content |
| `PUT` | `/projects/{slug}/wiki/{page}/` | Create or update page |

## Branches & Tags

| Method | Path | Description |
|---|---|---|
| `GET` | `/projects/{slug}/branches/` | List branches |
| `POST` | `/projects/{slug}/branches/` | Create branch |
| `GET` | `/projects/{slug}/tags/` | List tags |
| `POST` | `/projects/{slug}/tags/` | Create tag |

## Releases

| Method | Path | Description |
|---|---|---|
| `GET` | `/projects/{slug}/releases/` | List releases |
| `POST` | `/projects/{slug}/releases/` | Create release |
| `GET` | `/projects/{slug}/releases/{id}/` | Release detail |

## Search

| Method | Path | Description |
|---|---|---|
| `GET` | `/search/?q=...` | Full-text search across projects, tickets, wiki |
| `GET` | `/projects/{slug}/search/?q=...` | Search within a project |

## CI Status

| Method | Path | Description |
|---|---|---|
| `POST` | `/projects/{slug}/ci/` | Report CI status for a commit |
| `GET` | `/projects/{slug}/ci/{hash}/` | Get CI status for a commit |

## Batch API

Send up to 25 API calls in a single HTTP request:

```
POST /api/v1/batch/
Content-Type: application/json

{
  "requests": [
    {"method": "GET", "path": "/projects/my-app/timeline/"},
    {"method": "GET", "path": "/projects/my-app/tickets/"},
    {"method": "GET", "path": "/projects/my-app/branches/"}
  ]
}
```

Returns an array of responses in the same order.

## Agent Workspaces

| Method | Path | Description |
|---|---|---|
| `POST` | `/projects/{slug}/workspaces/` | Claim an agent workspace |
| `GET` | `/projects/{slug}/workspaces/{id}/` | Workspace status |
| `DELETE` | `/projects/{slug}/workspaces/{id}/` | Release workspace |
| `POST` | `/projects/{slug}/workspaces/{id}/commit/` | Commit workspace changes |

## Code Reviews

| Method | Path | Description |
|---|---|---|
| `POST` | `/projects/{slug}/reviews/` | Request a code review |
| `GET` | `/projects/{slug}/reviews/{id}/` | Review detail + comments |
| `POST` | `/projects/{slug}/reviews/{id}/approve/` | Approve review |
| `POST` | `/projects/{slug}/reviews/{id}/merge/` | Merge approved branch |

## SSE Event Stream

```
GET /api/v1/projects/{slug}/events/
Accept: text/event-stream
```

Streams real-time events: `checkin`, `ticket.created`, `ticket.updated`,
`wiki.updated`, `review.approved`, `ci.status`.

## MCP Server

The MCP server is available at `/mcp/` and exposes 17 tools:

| Tool | Description |
|---|---|
| `list_projects` | List accessible projects |
| `get_project` | Project detail and stats |
| `get_timeline` | Commit history |
| `get_commit` | Single commit with diff |
| `list_tickets` | List tickets with filters |
| `create_ticket` | Open a new ticket |
| `update_ticket` | Update ticket status/fields |
| `list_wiki_pages` | Wiki page index |
| `get_wiki_page` | Wiki page content |
| `update_wiki_page` | Create or update wiki page |
| `list_branches` | Branch list |
| `create_branch` | Create branch from commit |
| `claim_workspace` | Claim an agent workspace |
| `commit_workspace` | Commit workspace changes |
| `release_workspace` | Release workspace |
| `request_review` | Request code review |
| `search` | Full-text search |
"""

AGENTIC_DEVELOPMENT_CONTENT = """\
# Agentic Development

## The Problem

GitHub's API enforces strict rate limits:
- 5,000 requests/hour for authenticated users
- Search API: 30 requests/minute
- Code search: 10 requests/minute

An AI coding agent making rapid-fire API calls — reading files, listing branches,
checking CI status — exhausts these limits in minutes.

## The Solution

Fossilrepo has no rate limits. The API talks directly to local SQLite files.
The MCP server exposes the same data through 17 tools without any throttling.

## Agent Workflow

### 1. Claim a Workspace

```bash
POST /api/v1/projects/my-app/workspaces/
{
  "agent_id": "claude-agent-1",
  "branch": "feature/new-auth"
}
```

Returns a workspace ID and a temporary branch. Other agents cannot claim the
same branch simultaneously.

### 2. Make Changes

The agent clones the project, checks out the workspace branch, and makes changes:

```bash
fossil clone http://localhost:8080/my-app my-app.fossil
fossil open my-app.fossil
fossil branch new feature/new-auth trunk
# ... make changes ...
fossil commit -m "Implement new auth flow"
fossil push
```

### 3. Commit via API

```bash
POST /api/v1/projects/my-app/workspaces/{id}/commit/
{
  "message": "Implement new auth flow",
  "files": ["auth/views.py", "auth/models.py"]
}
```

### 4. Request Review

```bash
POST /api/v1/projects/my-app/reviews/
{
  "workspace_id": "{id}",
  "description": "New auth flow — replaces session tokens with JWTs"
}
```

### 5. Merge

Once approved (human or automated):

```bash
POST /api/v1/projects/my-app/reviews/{review_id}/merge/
```

The branch is merged into trunk and the workspace is released.

## Multi-Agent Safety

Multiple agents can work on the same project simultaneously, each in their own
workspace (branch). The workspace claim is atomic — no two agents get the same
branch. Reviews provide a coordination point before merge.

Fossilrepo tracks which agent owns each workspace. If an agent crashes without
releasing, its workspace times out after the configured `WORKSPACE_TIMEOUT`
(default: 4 hours).

## Why Fossil for Agents?

| Feature | GitHub | Fossilrepo |
|---|---|---|
| Rate limits | 5,000/hour | None |
| API latency | ~100ms (remote) | <1ms (local SQLite) |
| Workspace isolation | PRs (remote) | Local branches |
| Offline capable | No | Yes |
| Built-in issue tracker | Yes | Yes (Fossil tickets) |
| Self-hosted | GitHub Enterprise only | Yes, free |

## MCP Integration

Configure your Claude agent with the fossilrepo MCP server:

```json
{
  "mcpServers": {
    "fossilrepo": {
      "url": "http://localhost:8000/mcp/",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}
```

The agent then has access to all 17 MCP tools with no rate limiting.
"""

SETUP_GUIDE_CONTENT = """\
# Setup Guide

## Docker Quick Start

```bash
# Clone the repo
fossil clone https://fossilrepo.io/projects/fossilrepo/ fossilrepo.fossil
fossil open fossilrepo.fossil --workdir fossilrepo
cd fossilrepo

# Start everything
docker compose up -d

# Run migrations and seed
docker compose exec django python manage.py migrate
docker compose exec django python manage.py seed_roles
docker compose exec django python manage.py seed_content
docker compose exec django python manage.py createsuperuser
```

## Default Services

| Service | Port | Purpose |
|---|---|---|
| Django (Gunicorn) | 8000 | Management UI |
| Fossil server | 8080 | VCS HTTP access |
| Caddy | 80/443 | SSL proxy, subdomain routing |
| PostgreSQL | 5432 | Application database |
| Redis | 6379 | Celery broker + cache |
| Celery worker | — | Background tasks |
| Celery beat | — | Scheduled tasks |
| Litestream | — | S3 replication |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | — | Django secret key (required) |
| `DATABASE_URL` | — | PostgreSQL DSN (required) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis DSN |
| `FOSSIL_DATA_DIR` | `/data/repos` | Where `.fossil` files are stored |
| `FOSSIL_PORT` | `8080` | Fossil server port |
| `ALLOWED_HOSTS` | `localhost` | Django `ALLOWED_HOSTS` |
| `DEBUG` | `false` | Django debug mode |
| `LITESTREAM_S3_BUCKET` | — | S3 bucket for backups |
| `LITESTREAM_S3_PATH` | `fossil-repos` | S3 key prefix |
| `GITHUB_CLIENT_ID` | — | OAuth (optional) |
| `GITHUB_CLIENT_SECRET` | — | OAuth (optional) |

## Constance Runtime Settings

Settings in **Admin > Super Admin > Constance** can be changed at runtime without
redeploying:

| Key | Default | Description |
|---|---|---|
| `FOSSIL_DATA_DIR` | `/data/repos` | Override repo storage path |
| `FOSSIL_HTTP_URL` | `http://localhost:8080` | Internal Fossil HTTP URL |
| `WORKSPACE_TIMEOUT_HOURS` | `4` | Agent workspace expiry |
| `MAX_REPOS_PER_ORG` | `100` | Repo limit per organization |
| `CLONE_TIMEOUT_SECONDS` | `120` | Max time for fossil clone |
| `SYNC_ENABLED` | `true` | Enable GitHub/GitLab sync |
| `REGISTRATION_OPEN` | `false` | Allow public registration |

## OAuth Setup

1. Create a GitHub OAuth App at GitHub → Settings → Developer Settings
2. Set **Callback URL** to `https://your-domain.com/auth/github/callback/`
3. Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` in your `.env`
4. Enable social auth in **Admin > Super Admin > Constance**

## Adding Repositories

### Via the UI

1. Go to **Projects → New Project**
2. Choose **Create new repo** or **Clone from URL**
3. If cloning, provide the source Fossil or Git URL

### Via the CLI (inside the container)

```bash
# Create a fresh repo
python manage.py fossil_create my-project

# Clone an existing Fossil repo
python manage.py fossil_clone https://example.com/fossil/my-project my-project

# Import a Git repo
python manage.py fossil_import_git https://github.com/org/repo my-project
```

## Production Deployment

For production:

1. Set `DEBUG=false` and `ALLOWED_HOSTS=your-domain.com`
2. Configure Caddy with your domain in `Caddyfile`
3. Point `LITESTREAM_S3_BUCKET` at a real S3 bucket
4. Use an external PostgreSQL (RDS, Supabase, etc.)
5. Set `REGISTRATION_OPEN=false` unless you want public signups
6. Configure SMTP for email notifications

Fossilrepo is stateless except for the `.fossil` files (replicated by Litestream)
and PostgreSQL. Deploy behind a load balancer without changes.
"""

ADMIN_GUIDE_CONTENT = """\
# Admin Guide

## User Management

Navigate to **Admin > Members** in the sidebar.

- **Create User** — username, email, name, password, org role
- **Edit User** — name, email, active status, staff access, role
- **Change Password** — from user detail page
- **Deactivate** — uncheck Active; login is blocked, history preserved

## Roles

Navigate to **Admin > Roles**.

| Role | Access |
|---|---|
| Admin | Full access |
| Manager | Manage projects, teams, members, pages |
| Developer | View projects, create tickets, contribute |
| Viewer | Read-only |

Click **Initialize Roles** if no roles exist (runs `seed_roles`).
Custom roles can be created with a specific permission set from the permission picker.

## Litestream Backups

Litestream replicates all `.fossil` files to S3 in real time.

```bash
# Check replication lag
docker compose exec litestream litestream replicate -config /etc/litestream.yml

# Restore a repo from S3
docker compose exec litestream litestream restore \
  -config /etc/litestream.yml \
  /data/repos/my-project.fossil
```

Point-in-time recovery: Litestream stores WAL frames. Restore to any point by
specifying a timestamp with `-timestamp`.

## Monitoring Endpoints

| Endpoint | Description |
|---|---|
| `/health/` | Application health (200 = OK) |
| `/metrics/` | Prometheus metrics (if enabled) |
| `/admin/celery-monitor/` | Celery task queue status |

## Audit Log

Navigate to **Admin > Audit Log**. Shows all model changes powered by
django-simple-history. Filter by model type to see changes to specific entities.

## Super Admin

Navigate to **Admin > Super Admin** (Django's built-in admin interface).

Use for: direct database access, Constance runtime settings, Celery task results
and beat schedule, advanced permissions, data import/export.

Most day-to-day operations should use the main UI, not Super Admin.
"""

ADMINISTRATION_CONTENT = """\
# Administration

## User Management

Navigate to **Admin > Members** in the sidebar.

### Creating Users

1. Click **Create User**
2. Fill in username, email, name, password
3. Optionally assign an org role

The user is automatically added as an organization member.

### Editing Users

Click a username to view their profile, then **Edit** to change:
- Name, email
- Active/inactive status
- Staff status (access to Super Admin)
- Org role assignment

### Changing Passwords

From the user detail page, click **Change Password**. Admins can change any
user's password. Users can change their own password from their profile page.

### Deactivating Users

Edit the user and uncheck **Active**. This prevents login without deleting the
account. The user's history and contributions are preserved.

## Roles

Navigate to **Admin > Roles**.

### Predefined Roles

| Role | Access Level |
|---|---|
| Admin | Full access to everything |
| Manager | Manage projects, teams, members, pages |
| Developer | Contribute: view projects, create tickets |
| Viewer | Read-only access to all content |

### Custom Roles

Click **Create Role** to define a custom role with a specific permission set.
The permission picker groups permissions by app (Organization, Projects, Pages, Fossil).

### Initializing Roles

If no roles exist, click **Initialize Roles** to create the four predefined roles.
This runs the `seed_roles` management command.

### How Roles Work

Each role maps to a Django Group with the same permissions. When a user is assigned
a role, their previous role group is removed and the new one added. Permissions are
synced automatically.

## Teams

Navigate to **Admin > Teams**.

Teams are groups of users that can be assigned to projects with specific access levels.

### Creating Teams

1. Click **New Team**
2. Enter name and description
3. Add members from the user list

### Assigning Teams to Projects

1. Go to the project overview
2. Click the project name → **Teams** section
3. Click **Add Team**
4. Select team and role (read/write/admin)

## Project Groups

Navigate to **Admin > Groups**.

Groups organize related projects together in the sidebar. For example, a "Fossil SCM"
group might contain the source code repo, forum repo, and docs repo.

### Creating Groups

1. Click **Create Group**
2. Enter name and description
3. Assign projects to the group via the project edit form

## Organization Settings

Navigate to **Admin > Settings**.

Configure the organization name, website, and description. This appears in the site
header and various admin pages.

## Audit Log

Navigate to **Admin > Audit Log**.

Shows all model changes across the application, powered by django-simple-history.
Filter by model type to see changes to specific entities.

## Super Admin

Navigate to **Admin > Super Admin**.

This is Django's built-in admin interface. Use it for:
- Direct database access to any model
- Constance runtime settings
- Celery task results and beat schedule
- Advanced permission management
- Data import/export

Most day-to-day operations should be done through the main UI, not Super Admin.

## Project Settings

Each project has its own settings tab (visible to project admins):

- **Repository Info** — filename, file size, project code, checkin/ticket/wiki counts
- **Remote URL** — configure upstream Fossil remote for pull/push/sync
- **Clone URLs** — HTTP and SSH clone URLs
- **Tokens** — project-scoped API tokens for CI/CD
- **Branch Protection** — per-branch rules: restrict push, require CI checks
- **Webhooks** — outbound webhooks on repository events

## Notification Settings

Users configure their own notification preferences at `/auth/notifications/`:

- **Delivery mode**: Immediate, Daily Digest, Weekly Digest, Off
- **Event types**: Checkins, Tickets, Wiki, Releases, Forum

Admins can view user preferences via Super Admin.
"""

# ---------------------------------------------------------------------------
# FossilSCM Guide (unchanged)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Pages to seed: (name, content) pairs in display order
# ---------------------------------------------------------------------------

PAGES = [
    ("Knowledge Base", KNOWLEDGE_BASE_CONTENT),
    # FossilRepo Docs
    ("Getting Started", GETTING_STARTED_CONTENT),
    ("Architecture Overview", ARCHITECTURE_OVERVIEW_CONTENT),
    ("Architecture", ARCHITECTURE_CONTENT),
    ("API Reference", API_REFERENCE_CONTENT),
    ("Agentic Development", AGENTIC_DEVELOPMENT_CONTENT),
    ("Setup Guide", SETUP_GUIDE_CONTENT),
    ("Admin Guide", ADMIN_GUIDE_CONTENT),
    ("Administration", ADMINISTRATION_CONTENT),
    # FossilSCM Guide
    ("FossilSCM Guide", FOSSIL_SCM_GUIDE_CONTENT),
]


class Command(BaseCommand):
    help = "Seed initial content (Knowledge Base, FossilRepo Docs, FossilSCM Guide). Idempotent."

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

        # Seed all wiki pages.
        for name, content in PAGES:
            self._seed_page(org, name, content)

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

    # Candidate local seed paths, in priority order.
    # FOSSILREPO_SEED_PATH overrides; /app/fossilrepo.fossil is bundled by COPY . .
    _LOCAL_SEED_CANDIDATES = [
        os.environ.get("FOSSILREPO_SEED_PATH", ""),
        "/app/fossilrepo.fossil",
    ]

    def _seed_fossilrepo_project(self, org):
        from constance import config

        from fossil.models import FossilRepository
        from projects.models import Project

        data_dir = Path(config.FOSSIL_DATA_DIR)
        fossil_filename = "fossilrepo.fossil"
        fossil_path = data_dir / fossil_filename

        # Ensure DB record exists if file is already on disk (any previous run).
        if fossil_path.exists():
            self.stdout.write("fossilrepo.fossil already on disk, ensuring DB record ...")
            self._ensure_project_record(org, fossil_path, fossil_filename)
            return

        data_dir.mkdir(parents=True, exist_ok=True)

        # 1. Try local seed candidates first (no network, no fossil binary needed).
        for candidate in self._LOCAL_SEED_CANDIDATES:
            if not candidate:
                continue
            src = Path(candidate)
            if src.exists() and src.is_file():
                self.stdout.write(f"Copying seed from {src} ...")
                shutil.copy2(src, fossil_path)
                self.stdout.write(self.style.SUCCESS(f"Seeded fossilrepo.fossil from local copy ({src})"))
                self._ensure_project_record(org, fossil_path, fossil_filename)
                return

        # 2. Fall back to cloning from fossilrepo.io.
        self.stdout.write("No local seed found — cloning fossilrepo from fossilrepo.io ...")
        try:
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

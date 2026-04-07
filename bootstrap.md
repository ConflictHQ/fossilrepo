# fossilrepo -- bootstrap

This is the primary conventions document. All agent shims (`CLAUDE.md`, `AGENTS.md`) point here.

An agent given this document and a business requirement should be able to generate correct, idiomatic code without exploring the codebase.

---

## What is fossilrepo

Omnibus-style installer for a self-hosted Fossil forge. One command gets you a full-stack code hosting platform: VCS, issues, wiki, timeline, web UI, SSL, and continuous backups -- all powered by Fossil SCM.

Think GitLab Omnibus, but for Fossil.

---

## Why Fossil

A Fossil repo is a single SQLite file. It contains the full VCS history, issue tracker, wiki, forum, and timeline. No external services. No rate limits. Portable -- hand the file to someone and they have everything.

For teams running CI agents or automation:
- Agents commit, file tickets, and update the wiki through one CLI and one protocol
- No API rate limits when many agents are pushing simultaneously
- The `.fossil` file IS the project artifact -- a self-contained archive
- Litestream replicates it to S3 continuously -- backup and point-in-time recovery for free

Fossil also has a built-in web UI (skinnable), autosync, peer-to-peer sync, and unversioned content storage (like Git LFS but built-in).

---

## What fossilrepo Does

fossilrepo packages everything needed to run a production Fossil server into one installable unit:

- **Fossil server** -- serves all repos from a single process
- **Caddy** -- SSL termination, subdomain-per-repo routing (`reponame.your-domain.com`)
- **Litestream** -- continuous SQLite replication to S3/MinIO (backup + point-in-time recovery)
- **CLI** -- repo lifecycle management (create, list, delete) and sync tooling
- **Sync bridge** -- mirror Fossil repos to GitHub/GitLab as downstream read-only copies

New project = `fossil init`. No restart, no config change. Litestream picks it up automatically.

---

## Server Stack

```
Caddy  (SSL termination, routing, subdomain per repo)
  +-- fossil server --repolist /data/repos/
        +-- /data/repos/
              |-- projecta.fossil
              |-- projectb.fossil
              +-- ...

Litestream -> S3/MinIO  (continuous replication, point-in-time recovery)
```

One binary serves all repos. The whole platform is: repo creation + subdomain provisioning + Litestream config.

### Sync Bridge

Mirrors Fossil to GitHub/GitLab as a downstream copy. Fossil is the source of truth.

Maps:
- Fossil commits -> Git commits
- Fossil tickets -> GitHub/GitLab Issues (optional, configurable)
- Fossil wiki -> repo docs (optional, configurable)

Triggered on demand or on schedule.

---

## Architecture

```
fossilrepo/
|-- config/          # Django settings, URLs, Celery
|-- core/            # Base models, permissions, middleware
|-- accounts/           # Session-based auth
|-- organization/    # Org + member management
|-- docker/          # Fossil-specific: Caddyfile, litestream.yml
|-- templates/       # HTMX templates
|-- _old_fossilrepo/ # Original server/sync/cli code (being ported)
+-- docs/            # Architecture guides
```

---

## What's Already Built

| Layer | What's there |
|---|---|
| Auth | Session-based auth (accounts), login/logout views with templates, rate limiting |
| Data | Postgres 16, `Tracking` base model (version, created/updated/deleted by+at, soft deletes, history) |
| API | Django views returning HTML (full pages + HTMX partials) |
| Permissions | Group-based via `P` enum, checked in every view |
| Async | Celery worker + beat, Redis broker |
| Admin | Django Admin with `BaseCoreAdmin` (import/export, tracking fields) |
| Infra | Docker Compose: postgres, redis, celery-worker, celery-beat, mailpit |
| CI | GitHub Actions: lint (Ruff) + tests (Postgres + Redis services) |
| Seed | `python manage.py seed` creates admin/viewer users, sample data |
| Frontend | HTMX 2.0 + Alpine.js 3 + Tailwind CSS, server-rendered templates |

---

## App Structure

| App | Purpose |
|---|---|
| `config` | Django settings, URLs, Celery configuration |
| `core` | Base models (Tracking, BaseCoreModel), admin (BaseCoreAdmin), permissions (P enum), middleware |
| `accounts` | Session-based authentication: login/logout views with rate limiting |
| `organization` | Organization + OrganizationMember models |
| `testdata` | `seed` management command for development data |

---

## Conventions

### Models

All business models inherit from one of:

**`Tracking`** (abstract) -- audit trails:
```python
from core.models import Tracking

class Invoice(Tracking):
    amount = models.DecimalField(...)
```
Provides: `version` (auto-increments), `created_at/by`, `updated_at/by`, `deleted_at/by`, `history` (simple_history).

**`BaseCoreModel(Tracking)`** (abstract) -- named entities:
```python
from core.models import BaseCoreModel

class Project(BaseCoreModel):
    visibility = models.CharField(...)
```
Adds: `guid` (UUID), `name`, `slug` (auto-generated, unique), `description`.

**Soft deletes:** call `obj.soft_delete(user=request.user)`, never `.delete()`.

**ActiveManager:** Use `objects` (excludes deleted) for queries, `all_objects` for admin.

---

### Views (HTMX Pattern)

Views return full pages for normal requests, HTMX partials for `HX-Request`:

```python
@login_required
def project_list(request):
    P.PROJECT_VIEW.check(request.user)
    projects = Project.objects.all()

    if request.headers.get("HX-Request"):
        return render(request, "projects/partials/project_table.html", {"projects": projects})

    return render(request, "projects/project_list.html", {"projects": projects})
```

**URL patterns** follow CRUD convention:
```python
urlpatterns = [
    path("", views.project_list, name="list"),
    path("create/", views.project_create, name="create"),
    path("<slug:slug>/", views.project_detail, name="detail"),
    path("<slug:slug>/edit/", views.project_update, name="update"),
    path("<slug:slug>/delete/", views.project_delete, name="delete"),
]
```

---

### Permissions

Group-based. Never user-based. Checked in every view.

```python
from core.permissions import P

P.PROJECT_VIEW.check(request.user)          # raises PermissionDenied if denied
P.PROJECT_ADD.check(request.user, raise_error=False)  # returns False instead
```

Template guards:
```html
{% if perms.projects.view_project %}
  <a href="{% url 'projects:list' %}">Projects</a>
{% endif %}
```

---

### Admin

All admin classes inherit `BaseCoreAdmin`:
```python
from core.admin import BaseCoreAdmin

@admin.register(Project)
class ProjectAdmin(BaseCoreAdmin):
    list_display = ("name", "slug", "visibility", "created_at")
    search_fields = ("name", "slug")
```

`BaseCoreAdmin` provides: audit fields as readonly, `created_by`/`updated_by` auto-set, import/export.

---

### Templates

- `base.html` -- layout with HTMX, Alpine.js, Tailwind CSS, CSRF injection, messages
- `includes/nav.html` -- navigation bar with permission guards
- `{app}/partials/*.html` -- HTMX partial templates (no `{% extends %}`)
- CSRF token sent with all HTMX requests via `htmx:configRequest` event

Alpine.js patterns for client-side interactivity:
```html
<div x-data="{ open: false }">
  <button @click="open = !open">Toggle</button>
  <div x-show="open" x-transition>Content</div>
</div>
```

---

### Tests

pytest + real Postgres. Assert against database state.

```python
@pytest.mark.django_db
class TestProjectCreate:
    def test_create_saves_project(self, admin_client, admin_user, org):
        response = admin_client.post(reverse("projects:create"), {
            "name": "New App", "visibility": "private", ...
        })
        assert response.status_code == 302
        project = Project.objects.get(name="New App")
        assert project.created_by == admin_user

    def test_create_denied_for_viewer(self, viewer_client):
        response = viewer_client.get(reverse("projects:create"))
        assert response.status_code == 403
```

Both allowed AND denied permission cases for every endpoint.

---

### Code Style

| Tool | Config |
|------|--------|
| Ruff (lint + format) | `pyproject.toml`, line length 140 |
| Import sorting | Ruff isort rules |
| Python version | 3.12+ |

Run `ruff check .` and `ruff format --check .` before committing.

---

## Adding a New App

```bash
# 1. Create the app
python manage.py startapp myapp

# 2. Add to INSTALLED_APPS in config/settings.py

# 3. Create models inheriting Tracking or BaseCoreModel

# 4. Create migrations
python manage.py makemigrations

# 5. Create admin (inherit BaseCoreAdmin)

# 6. Create views with @login_required + P.PERMISSION.check()

# 7. Create URL patterns (list, detail, create, update, delete)

# 8. Create templates (full page + HTMX partials)

# 9. Add permission entries to core/permissions.py P enum

# 10. Write tests (allowed + denied)
python -m pytest --cov -v
```

---

## Ports (local Docker)

| Service | URL |
|---|---|
| Django | http://localhost:8000 |
| Django Admin | http://localhost:8000/admin/ |
| Health | http://localhost:8000/health/ |
| Mailpit | http://localhost:8025 |
| Postgres | localhost:5432 |
| Redis | localhost:6379 |

---

## Common Commands

```bash
make up              # Start the stack
make build           # Build and start
make down            # Stop the stack
make migrate         # Run migrations
make migrations      # Create migrations
make seed            # Load dev fixtures
make test            # Run tests with coverage
make lint            # Run Ruff check + format
make superuser       # Create Django superuser
make shell           # Shell into container
make logs            # Tail Django logs
```

---

## Platform Vision (fossilrepos.com)

GitLab model:
- **Self-hosted** -- open source, run it yourself. fossilrepo is the tool.
- **Managed** -- fossilrepos.com, hosted for you. Subdomain per repo, modern UI, billing.

The platform is Fossil's built-in web UI with a modern skin + thin API wrapper + authentication. Not a rewrite -- Fossil already does the hard parts. The value is the hosting and UX polish.

Not being built yet -- get the self-hosted tool right first.

---

## License

MIT.

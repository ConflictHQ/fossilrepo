# Fossilrepo Django + HTMX

Server-rendered Django with HTMX for dynamic behavior and Alpine.js for lightweight client state. Tailwind CSS for styling. Choose this for content-heavy CRUD, admin-centric tools, and apps where server-rendered simplicity beats a full SPA.

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 5 (Python 3.12+) |
| Frontend | HTMX 2.0 + Alpine.js 3 + Tailwind CSS |
| Database | PostgreSQL 16 |
| Cache/Broker | Redis 7 |
| Job Queue | Celery + Redis |
| Auth | Session-based (httpOnly cookies) |
| Linter | Ruff |
| Package Manager | uv |

## Quick Start

```bash
# Start the stack
docker compose up -d --build

# Run migrations and seed data
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed

# Open the app
open http://localhost:8000
```

**Default users:**
- `admin` / `admin` (superuser, full access)
- `viewer` / `viewer` (view-only permissions)

## Architecture

```
Browser
  +-- Django Templates + HTMX + Alpine.js + Tailwind CSS
        |
        v (standard HTTP + HTMX partial responses)
        |
  Django 5 (Views, ORM, Permissions)
        |-- Celery (async tasks)
        |-- PostgreSQL 16 (data)
        +-- Redis 7 (cache, sessions, broker)
```

No separate frontend service. Django serves everything — templates, static files, and HTMX partials.

## Endpoints

| Path | Description |
|------|------------|
| `/` | Redirects to dashboard |
| `/dashboard/` | Main dashboard |
| `/items/` | Item list with HTMX search |
| `/items/create/` | Create item form |
| `/items/<slug>/` | Item detail |
| `/items/<slug>/edit/` | Edit item form |
| `/items/<slug>/delete/` | Delete confirmation |
| `/auth/login/` | Login page |
| `/auth/logout/` | Logout |
| `/admin/` | Django admin |
| `/health/` | Health check (JSON) |

## Development

```bash
# Local development (without Docker)
uv sync --all-extras
POSTGRES_HOST=localhost POSTGRES_PORT=5434 uv run python manage.py runserver

# Run tests
make test

# Run linter
make lint
```

## Conventions

See [`bootstrap.md`](bootstrap.md) for the full conventions document.

Key patterns:
- All models inherit `Tracking` (audit trails) or `BaseCoreModel` (named entities with UUID)
- Soft deletes only — never call `.delete()` on business objects
- Group-based permissions checked in every view via `P.PERMISSION.check(user)`
- HTMX partials for dynamic updates, Alpine.js for client-side state
- Tests against real Postgres, both allowed and denied permission cases

---

Fossilrepo is a [CONFLICT](https://weareconflict.com) brand. CONFLICT is a registered trademark of CONFLICT LLC.

# Claude -- fossilrepo

Primary conventions doc: [`bootstrap.md`](bootstrap.md)

Read it before writing any code.

## Project Overview

fossilrepo is an omnibus-style installer for a self-hosted Fossil forge. Django+HTMX management layer wrapping Fossil SCM server infrastructure with Caddy (SSL/routing), Litestream (S3 backups), and a sync bridge to GitHub/GitLab. Open source (MIT).

## Stack

- **Backend**: Django 5 (Python 3.12+)
- **Frontend**: HTMX 2.0 + Alpine.js 3 + Tailwind CSS (CDN)
- **API**: Django views returning HTML (full pages + HTMX partials)
- **ORM**: Django ORM with `Tracking` and `BaseCoreModel` base classes
- **Auth**: Session-based (Django native, httpOnly cookies)
- **Permissions**: Group-based via `P` enum (`core/permissions.py`)
- **Jobs**: Celery + Redis
- **Database**: PostgreSQL 16
- **Linter**: Ruff (check + format), max line length 140
- **Fossil SCM**: C binary, serves repos (each repo is a single .fossil SQLite file)
- **Caddy**: SSL termination + subdomain routing to Fossil instances
- **Litestream**: Continuous SQLite-to-S3 replication for backups

## Repository Structure

```
fossilrepo/
├── core/                  # Base models, permissions, shared utilities
├── auth1/                 # Authentication
├── organization/          # Org/team management
├── items/                 # Repo item models
├── config/                # Django settings
├── templates/             # Django + HTMX templates
├── static/                # Static assets
├── docker/                # Caddy, Litestream container configs
├── fossil-platform/       # Old exploration (Flask + React), kept for reference
├── tests/                 # pytest
├── docs/                  # Architecture, guides
└── bootstrap.md           # Project bootstrap doc -- read first
```

## Claude-specific notes

- Prefer `Edit` over rewriting whole files.
- Run `ruff check .` and `ruff format --check .` before committing.
- Never expose integer PKs in URLs or templates -- use `slug` or `guid`.
- Auth check at the top of every view -- use `@login_required` + `P.PERMISSION.check(request.user)`.
- Soft-delete only: call `item.soft_delete(user=request.user)`, never `.delete()`.
- HTMX partials: check `request.headers.get("HX-Request")` to return partial vs full page.
- CSRF: HTMX requests include CSRF token via `htmx:configRequest` event in `base.html`.
- Tests: pytest + real Postgres, assert against DB state. Both allowed and denied permission cases.
- Fossil is the source of truth; Git remotes are downstream mirrors.

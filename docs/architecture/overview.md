# Architecture

## Overview

FossilRepo is a Django web application that wraps Fossil SCM repositories with a modern UI. It reads `.fossil` files directly as SQLite databases for speed, and uses the `fossil` CLI binary for write operations to maintain artifact integrity.

```
Browser (HTMX + Alpine.js + Tailwind CSS)
  |
  v
Django 5 (views, ORM, permissions)
  |
  |-- FossilReader (direct SQLite reads, ?mode=ro)
  |-- FossilCLI (subprocess for writes: commit, ticket, wiki, push/pull)
  |-- fossil http (CGI proxy for clone/push/pull)
  |
  |-- PostgreSQL 16 (app data: users, orgs, teams, projects, settings)
  |-- Redis 7 (Celery broker, cache)
  |-- Celery (background: metadata sync, git mirror, webhooks, digest)
  |
  v
.fossil files (SQLite: code + wiki + tickets + forum + technotes)
  |
  v
Litestream --> S3 (continuous SQLite replication)
```

## Core Components

### FossilReader

Opens `.fossil` repository files directly as read-only SQLite databases. No network calls to a running Fossil server. Python's `sqlite3` module with `?mode=ro` URI.

Handles:
- Blob decompression (zlib with 4-byte size prefix)
- Delta chain resolution (Fossil's delta-encoded artifacts)
- Julian day timestamp conversion
- Timeline queries, file tree at any checkin, ticket/wiki/forum reads
- Commit activity aggregation, contributor stats, search

### FossilCLI

Thin subprocess wrapper around the `fossil` binary. Used for all write operations:
- Repository init, clone, push, pull, sync
- Ticket create/change, wiki create/commit
- Technote creation, blame, Pikchr rendering
- Git export for mirror sync
- Tarball and zip archive generation
- Unversioned file management
- Artifact shunning

All calls set `USER=fossilrepo` in the environment and call `ensure_default_user()` to prevent "cannot figure out who you are" errors.

### HTTP Sync Proxy

The `fossil_xfer` view proxies Fossil's wire protocol through Django. Clients clone/push/pull via:

```
fossil clone http://your-server/projects/<slug>/fossil/xfer repo.fossil
```

Django handles authentication and access control. Public repos allow anonymous pull (no `--localauth`). Authenticated users with write access get full push via `--localauth`. Branch protection rules are enforced at this layer.

### SSH Sync

An `sshd` instance runs on port 2222 with a restricted `fossil-shell` forced command. Users upload SSH public keys via their profile. The `authorized_keys` file is regenerated from the database on key add/remove.

```
fossil clone ssh://fossil@host:2222/<slug> repo.fossil
```

## Data Architecture

### Two Databases

1. **PostgreSQL** — application state: users, organizations, teams, projects, releases, webhooks, API tokens, workspace claims, code reviews, notification preferences
2. **Fossil .fossil files** — repository data: code history, tickets, wiki, forum, technotes, unversioned files

### Model Base Classes

- `Tracking` (abstract) — `version`, `created_at/by`, `updated_at/by`, `deleted_at/by`, `history` (django-simple-history)
- `BaseCoreModel(Tracking)` — adds `guid` (UUID4), `name`, `slug` (auto-generated), `description`
- Soft deletes only: `obj.soft_delete(user=request.user)`, never `.delete()`
- `ActiveManager` on `objects` excludes soft-deleted; `all_objects` includes them

### Permission Model

Two layers:
1. **Org-level roles** (Admin/Manager/Developer/Viewer) — Django Groups with permission bundles, assignable per user
2. **Project-level RBAC** (read/write/admin) — per team, via ProjectTeam model

Project visibility:
- **Public** — anyone can read (including anonymous)
- **Internal** — authenticated users can read
- **Private** — team members only

### Encryption

SSH keys and OAuth tokens encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256), keyed from Django's `SECRET_KEY`. Implemented as `EncryptedTextField` in `core/fields.py`.

## Infrastructure

### Docker (Omnibus)

Single multi-stage Dockerfile:
1. Stage 1: compile Fossil 2.24 from source (Debian bookworm)
2. Stage 2: Python 3.12 runtime with Fossil binary, sshd, gosu

Entrypoint starts sshd as root, drops to unprivileged `app` user for gunicorn.

### Celery Tasks

| Task | Schedule | Purpose |
|------|----------|---------|
| sync_metadata | Every 5 min | Update repo stats (size, checkin count) |
| check_upstream | Every 15 min | Check for new upstream artifacts |
| dispatch_notifications | Every 5 min | Send pending email notifications |
| send_digest (daily) | Every 24h | Daily notification digest |
| send_digest (weekly) | Every 7d | Weekly notification digest |
| dispatch_webhook | On event | Deliver webhook with retry |
| run_git_sync | On schedule | Git mirror export |

### Caddy

SSL termination and subdomain routing. Each repo can get its own subdomain.

### Litestream

Continuous SQLite-to-S3 replication for `.fossil` files. Point-in-time recovery.

## Django Apps

| App | Purpose |
|-----|---------|
| `core` | Base models, permissions, pagination, sanitization, encryption |
| `accounts` | Login/logout, SSH keys, user profile, personal access tokens |
| `organization` | Org settings, teams, members, roles |
| `projects` | Projects, project groups, project stars, team assignment |
| `pages` | FossilRepo KB (knowledge base articles) |
| `fossil` | Everything Fossil: reader, CLI, views, sync, webhooks, releases, CI, workspaces, reviews |
| `mcp_server` | MCP server for AI tool integration |

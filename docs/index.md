# Fossilrepo

**Self-hosted Fossil forge. One command, full-stack code hosting.**

![FossilRepo Tour](fossilrepo-tour.gif)

Fossilrepo is an omnibus-style installer for a production Fossil SCM server. It packages Fossil, Caddy (SSL/routing), Litestream (S3 backups), and a Django management layer into a single deployable unit.

Think GitLab Omnibus, but for Fossil.

## Why Fossil?

A Fossil repository is a single SQLite file containing the full VCS history, issue tracker, wiki, forum, and timeline. No external services. No rate limits. Portable -- hand the file to someone and they have everything.

- **Single-file repos** -- each `.fossil` file is the entire project
- **Built-in everything** -- issues, wiki, forum, timeline, web UI
- **No API rate limits** -- ideal for CI agents and automation
- **Litestream replication** -- continuous backup to S3 for free

## What You Get

| Component | Role |
|---|---|
| **Fossil server** | Serves all repos from a single process |
| **Caddy** | SSL termination, subdomain-per-repo routing |
| **Litestream** | Continuous SQLite replication to S3/MinIO |
| **Django management UI** | Repository lifecycle, user management, dashboards |
| **Sync bridge** | Mirror Fossil repos to GitHub/GitLab (read-only) |
| **Celery workers** | Background sync, scheduled tasks |

## Quick Start

```bash
# Clone from Fossil
fossil clone https://fossilrepo.io/fossilrepo fossilrepo.fossil
fossil open fossilrepo.fossil --workdir fossilrepo
cd fossilrepo

# Start the full stack
make build

# Seed development data
make seed

# Open the dashboard
open http://localhost:8000
```

!!! note "Git mirror available"
    A read-only mirror is maintained on GitHub for convenience:
    `git clone https://github.com/ConflictHQ/fossilrepo.git`

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

## License

MIT License -- Copyright (c) 2026 CONFLICT LLC.

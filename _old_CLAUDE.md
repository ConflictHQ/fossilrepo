# CLAUDE.md -- fossilrepo

## Project Overview

fossilrepo is a self-hosted Fossil SCM server infrastructure tool. It provides Docker + Caddy + Litestream hosting for Fossil repositories, a CLI wrapper around fossil commands, and a sync bridge to mirror Fossil repos to GitHub/GitLab.

Open source (MIT). Part of the CONFLICT ecosystem.

## Repository Structure

```
fossilrepo/
├── fossilrepo/                # Python package
│   ├── server/                # Fossil server management (Docker, Caddy, Litestream)
│   │   ├── config.py          # Pydantic server configuration
│   │   └── manager.py         # Repo lifecycle (create, delete, list)
│   ├── sync/                  # Fossil → Git mirror
│   │   ├── mirror.py          # Core sync logic (commits, tickets, wiki)
│   │   └── mappings.py        # Data models for Fossil↔Git mappings
│   └── cli/                   # Click CLI
│       └── main.py            # CLI entrypoint (server, repo, sync commands)
├── docker/                    # Container configs
│   ├── Dockerfile             # Fossil + Caddy + Litestream
│   ├── docker-compose.yml     # Local dev stack
│   ├── Caddyfile              # Subdomain routing
│   └── litestream.yml         # S3 replication
├── tests/                     # pytest, mirrors fossilrepo/
├── docs/                      # Architecture, guides
├── fossil-platform/           # Old exploration (Flask + React), kept for reference
├── bootstrap.md               # Project bootstrap doc — read first
└── AGENTS.md                  # Agent conventions pointer
```

## Key Conventions

- Python 3.11+, typed with Pydantic models
- Click for CLI, Rich for terminal output
- Ruff for linting, pytest for testing
- Fossil is the source of truth; Git remotes are downstream mirrors
- Server infra: Docker + Caddy (SSL, subdomain routing) + Litestream (S3 replication)
- Each repo is a single .fossil file (SQLite) — Litestream replicates it continuously

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## CLI

```bash
fossilrepo server start|stop|status
fossilrepo repo create|list|delete
fossilrepo sync run|status
```

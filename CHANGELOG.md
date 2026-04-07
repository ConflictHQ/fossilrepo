# Changelog

All notable changes to Fossilrepo are documented here.

## [0.1.0] - 2026-04-07

Initial open source release.

### Features

- **Code browser** with directory navigation, syntax highlighting, line numbers, permalinks, blame with age coloring, file history, raw download
- **Timeline** with DAG graph (fork/merge connectors, color-coded branches, merge diamonds, leaf indicators), keyboard navigation, HTMX infinite scroll, RSS feed
- **Diffs** with unified and side-by-side views, syntax highlighting via highlight.js, line-level permalinks
- **Tickets** with full CRUD (create, edit, close/reopen, comment), filters, pagination, CSV export
- **Wiki** with Markdown + Fossil markup + Pikchr diagrams, create/edit, right-sidebar TOC
- **Forum** with threaded discussions, create threads, post replies (Django-backed + Fossil-native)
- **Releases** with versioned tags, markdown changelogs, file attachments, download counts, draft/prerelease support
- **Branches, tags, technotes** list views
- **Search** across checkins, tickets, and wiki
- **Contributor profiles** with activity views
- **Repository statistics** with Chart.js visualizations
- **Fossil Guide** serving bundled Fossil documentation

### Sync & Integration

- **Upstream sync** — pull from remote Fossil repositories
- **HTTP sync** — clone/push/pull proxied through Django via `fossil http` CGI mode
- **SSH sync** — sshd in container with restricted `fossil-shell` forced command
- **Git mirror** — push to GitHub/GitLab via OAuth or SSH key auth
- **Webhooks** — outbound HTTP webhooks with HMAC-SHA256, retry, delivery logs

### Organization & Access

- **Organization** settings with member and team management
- **User CRUD** — create, edit, deactivate, change password
- **Team management** — create teams, assign members
- **Project-level RBAC** — read/write/admin roles via team assignment
- **Project visibility** — public, internal, private
- **User SSH keys** — upload/manage public keys for SSH access

### Infrastructure

- **Omnibus Docker** — multi-stage build with Fossil 2.24 compiled from source
- **Caddy** config for SSL and subdomain routing
- **Litestream** config for SQLite-to-S3 replication
- **Celery** tasks for metadata sync, upstream checks, webhook dispatch, notifications
- **Encrypted storage** — Fernet/AES-128-CBC for SSH keys and OAuth tokens at rest
- **Dark/light theme** with system preference detection
- **Keyboard shortcuts** (j/k/Enter navigation, / for search, ? for help)

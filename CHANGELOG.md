# Changelog

All notable changes to Fossilrepo are documented here.

## [0.1.1] - 2026-04-13

### Added

- **Chat** — per-project real-time messaging backed by HTMX polling; gated behind `FEATURE_CHAT` flag (off by default)
- **Bundle export/import** — download a Fossil bundle for a branch or checkin, upload and import bundles; admin-only
- **Feature flags** — runtime on/off switches for optional features via Constance (`FEATURE_CHAT`, `FEATURE_RELEASES`, `FEATURE_SYNC`, `FEATURE_FILES`); all default off so new installs start minimal
- **Ticket priority field** — Priority (Critical / Important / Minor / Zero) added to ticket create form

### Fixed

- **Wiki links** — internal links in rendered wiki content were missing `/page/` in the URL (e.g. `.../wiki/Architecture` → `.../wiki/page/Architecture`); all three codepaths that generate wiki hrefs are now correct
- **DAG fork connectors** — fork branch connectors were drawn at the newest commit on a branch instead of the actual fork point; now correctly drawn where the branch diverges from its parent
- **Ticket 500 errors** — ticket list and detail views no longer 500 on malformed or missing ticket data
- **Diffs** — switched from Python difflib to Fossil's native diff engine for accurate delta computation

### Changed

- All timestamps across code browser, timeline, ticket, wiki, and technote views now show explicit `UTC` suffix
- UI polish: stronger card/input/table/button borders and definition throughout
- Ticket list defaults to Open status filter instead of showing all tickets
- Docs site now Fossil-forward: primary clone links point to Fossil instance; GitHub listed as mirror
- Author attribution updated to Leo Mata & CONFLICT LLC

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

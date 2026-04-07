# Features

## Code Browser
- Directory navigation with breadcrumbs and file size display
- Syntax-highlighted source view with line numbers and permalinks
- Copy-link popover on line number click
- Blame with age-based coloring (newest = red, oldest = gray)
- File history showing all checkins that touched a file
- Raw file download
- Rendered preview for Markdown, HTML, and other markup
- README auto-rendering at directory level

## Timeline
- DAG graph with fork/merge connectors and color-coded branches (8-color palette)
- Merge commit diamonds, leaf indicators (open circles)
- Date headers grouping commits by day
- Keyboard navigation (j/k to move, Enter to open)
- HTMX infinite scroll for seamless loading
- Event type filtering (checkins, wiki, tickets)
- RSS feed

## Diffs
- Unified and side-by-side view (toggle with localStorage preference)
- Syntax highlighting via highlight.js (auto-detected from file extension)
- Color-coded additions (green) and deletions (red)
- Line-level permalinks
- Compare any two checkins
- Fossil delta decoding for accurate diff computation

## Tickets
- Full CRUD: create, edit, close/reopen, add comments
- Filter by status, type, priority, severity
- Pagination with configurable per-page (25/50/100)
- Live search via HTMX
- CSV export
- Custom field definitions (text, textarea, select, checkbox, date, URL)
- Custom SQL ticket reports with injection prevention
- Per-page count selector

## Wiki
- Markdown + Fossil wiki markup + raw HTML rendering
- Pikchr diagram rendering (via fossil CLI)
- Create and edit pages
- Right-sidebar table of contents
- Internal link rewriting (Fossil URLs mapped to app URLs)
- Footnotes, tables, fenced code blocks

## Forum
- Threaded discussions (Fossil-native + Django-backed posts)
- Create new threads with markdown body
- Post replies with threading
- Merged view showing both Fossil and Django posts

## Releases
- Versioned releases with tag names and markdown changelogs
- Source code archives: tar.gz and zip (via fossil tarball/zip)
- File attachments with download counts
- Draft and prerelease support
- CRUD for authorized users

## Technotes
- Create and edit developer journal entries
- Markdown body with preview
- Timestamped, shown in timeline

## Unversioned Files
- Browse Fossil's unversioned content (equivalent to Git LFS)
- File list with size and date
- Download individual files
- Admin upload via fossil uv CLI

## Branches, Tags, Technotes
- List all branches with open/closed status
- List all tags
- Searchable and paginated

## Search
- Full-text search across checkins, tickets, and wiki pages
- Global search shortcut (/ key)
- Per-project scoped search

## Sync
- Pull from upstream Fossil remotes
- Push to downstream Fossil remotes
- Bidirectional sync
- Git mirror to GitHub/GitLab via OAuth or SSH key auth
- Multiple mirrors per repo, each with own schedule and direction
- Configurable sync modes: on-change, scheduled (cron), both, disabled
- Clone/push/pull over HTTP (fossil http CGI proxy)
- Clone/push/pull over SSH (port 2222, forced command)

## Webhooks
- Outbound HTTP webhooks on checkin, ticket, wiki, release events
- HMAC-SHA256 signed payloads
- Exponential backoff retry (3 attempts)
- Delivery log with response status and timing
- Per-project webhook configuration

## CI Status Checks
- External API for CI systems to POST build status per checkin
- Bearer token authentication
- SVG badge endpoint for embedding in READMEs
- Status display on checkin detail page (green/red/yellow icons)

## Releases
- Create/edit/delete versioned releases
- Link to Fossil checkin
- Markdown changelog body
- Source code download (tar.gz, zip)
- File attachments with download tracking
- Draft and prerelease flags

## Organization Management
- Single-org model with settings, website, description
- Member management: create, edit, deactivate, change password
- Team management: create, assign members
- Project groups: organize related repos under a group header
- Project-level team roles: read, write, admin

## Roles and Permissions
- Predefined roles: Admin, Manager, Developer, Viewer
- Custom role creation with permission picker (grouped by app)
- Role assignment on user create/edit
- Permissions synced to Django Groups automatically
- Two-layer model: org-level roles + project-level RBAC

## User Profiles
- Personal profile page: name, email, @handle, bio, location, website
- SSH key management with encrypted storage
- Personal access tokens (frp_ prefix, hash-only storage)
- Notification preferences (immediate/daily/weekly/off + event toggles)
- Change password

## Project Features
- Project starring with counts
- Explore/discover page for public projects (sort by stars/recent/name)
- Project groups for organizing related repos
- Public/internal/private visibility
- Anonymous access for public repos (all read views)

## API Tokens and Deploy Keys
- Project-scoped API tokens with SHA-256 hashed storage
- Token shown once on creation, never stored in plaintext
- Configurable permissions and expiry
- Last-used tracking

## Branch Protection
- Per-branch protection rules with glob pattern matching
- Restrict push to admins only
- Required CI status check contexts
- Enforced on HTTP sync, CLI push/sync, and SSH push

## Artifact Shunning
- Admin UI for permanently removing artifacts
- Type-to-confirm safety (must enter first 8 chars of UUID)
- Calls fossil shun CLI
- Irreversible with clear warning

## SQLite Explorer
- Visual schema map with category-colored table cards
- SVG relationship graph showing Fossil's internal table connections
- HTMX-powered table browser with column definitions and paginated data
- Custom SQL query runner (SELECT only, validated against injection)
- Admin-only access

## Audit Log
- Unified view of all model changes via django-simple-history
- Filter by model type (Project, Organization, Team, Repository)
- Shows user, action (Created/Changed/Deleted), timestamp
- Superuser/org-admin access

## Email Notifications
- HTML email templates (dark themed, inline CSS for email clients)
- Immediate delivery per event
- Daily/weekly digest mode
- Per-user event type toggles (checkins, tickets, wiki, releases, forum)
- Unsubscribe links

## Agentic Development Platform
- MCP server with 17 tools for AI assistant integration
- JSON API: 10+ read endpoints with Bearer token auth
- Batch API: execute up to 25 API calls in one request
- Agent workspaces: isolated Fossil branches per agent
- Atomic ticket claiming for multi-agent coordination
- Server-Sent Events for real-time notifications
- Code review API: submit diffs, comment, approve, merge

## UI/UX
- Dark/light theme with system preference detection
- Collapsible sidebar with project tree navigation
- Keyboard shortcuts (j/k, Enter, /, ?)
- Consistent pagination (25/50/100 per-page selector) across all lists
- HTMX live search with 300ms debounce
- Mobile responsive (slide-out drawer)
- Custom branded error pages (403, 404, 500)
- Public nav for anonymous users (logo, Explore, Sign in)

## Infrastructure
- Omnibus Docker image (Fossil compiled from source)
- Multi-arch builds (amd64 + arm64)
- Caddy for SSL termination and subdomain routing
- Litestream for continuous SQLite-to-S3 replication
- Supply chain attestations (SLSA provenance + SBOM)
- Non-root container execution (gosu privilege dropping)
- Celery Beat for scheduled tasks

# API Reference

FossilRepo provides a JSON API for programmatic access and an MCP server for AI tool integration.

## Authentication

All API endpoints accept authentication via:

1. **Bearer token** (recommended for automation):
   ```
   Authorization: Bearer frp_abc123...
   ```
   Tokens can be project-scoped (API Token) or user-scoped (Personal Access Token).

2. **Session cookie** (for browser-based testing):
   Log in via the web UI, then call API endpoints in the same browser session.

## JSON API Endpoints

Base URL: `/projects/<slug>/fossil/api/`

### Project

**GET /api/project** — Project metadata
```json
{"name": "FossilRepo", "slug": "fossilrepo", "description": "...", "visibility": "public", "star_count": 5}
```

### Timeline

**GET /api/timeline** — Recent checkins
- `?page=1` — Page number
- `?per_page=25` — Items per page
- `?branch=trunk` — Filter by branch

### Tickets

**GET /api/tickets** — Ticket list
- `?status=Open` — Filter by status
- `?page=1&per_page=25` — Pagination

**GET /api/tickets/\<uuid\>** — Single ticket with comments

**GET /api/tickets/unclaimed** — Tickets available for agent claiming

**POST /api/tickets/\<uuid\>/claim** — Claim ticket for exclusive work
```json
{"agent_id": "claude-session-abc"}
```

**POST /api/tickets/\<uuid\>/release** — Release a claim

**POST /api/tickets/\<uuid\>/submit** — Submit completed work
```json
{"summary": "Fixed by...", "workspace": "agent-fix-123"}
```

### Wiki

**GET /api/wiki** — Wiki page list
**GET /api/wiki/\<name\>** — Page content (raw + rendered HTML)

### Branches and Tags

**GET /api/branches** — All branches with open/closed status
**GET /api/tags** — All tags

### Releases

**GET /api/releases** — Release list with assets

### Search

**GET /api/search?q=term** — Search across checkins, tickets, wiki

### CI Status

**POST /api/status** — Report CI build status (Bearer token required)
```json
{"checkin": "abc123", "context": "ci/tests", "state": "success", "description": "All tests passed", "target_url": "https://ci.example.com/123"}
```

**GET /api/status/\<checkin_uuid\>/badge.svg** — SVG status badge

### Batch API

**POST /api/batch** — Execute up to 25 API calls in one request
```json
{"requests": [
  {"method": "GET", "path": "/api/timeline", "params": {"per_page": 5}},
  {"method": "GET", "path": "/api/tickets", "params": {"status": "Open"}},
  {"method": "GET", "path": "/api/wiki/Home"}
]}
```

### Agent Workspaces

**GET /api/workspaces** — List active workspaces
**POST /api/workspaces/create** — Create isolated workspace
```json
{"name": "fix-auth-bug", "agent_id": "claude-abc", "description": "Fixing auth issue"}
```
**GET /api/workspaces/\<name\>** — Workspace details
**POST /api/workspaces/\<name\>/commit** — Commit changes
**POST /api/workspaces/\<name\>/merge** — Merge back to trunk
**DELETE /api/workspaces/\<name\>/abandon** — Abandon workspace

### Code Reviews

**GET /api/reviews** — List reviews (filterable by status)
**POST /api/reviews/create** — Submit code for review
```json
{"title": "Fix null pointer", "description": "...", "diff": "--- a/...", "files_changed": ["src/auth.py"]}
```
**GET /api/reviews/\<id\>** — Review with comments
**POST /api/reviews/\<id\>/comment** — Add review comment
**POST /api/reviews/\<id\>/approve** — Approve
**POST /api/reviews/\<id\>/request-changes** — Request changes
**POST /api/reviews/\<id\>/merge** — Merge approved review

### Server-Sent Events

**GET /api/events** — Real-time event stream
```
event: checkin
data: {"uuid": "abc123", "user": "dev", "comment": "Fix bug"}

event: claim
data: {"ticket": "def456", "agent": "claude-abc", "status": "claimed"}

event: workspace
data: {"name": "fix-auth", "branch": "workspace/fix-auth", "status": "merged"}
```

## MCP Server

The MCP (Model Context Protocol) server gives AI tools native access to FossilRepo.

### Setup

```bash
pip install fossilrepo
fossilrepo-mcp
```

### Claude Code Configuration

```json
{
  "mcpServers": {
    "fossilrepo": {
      "command": "fossilrepo-mcp"
    }
  }
}
```

### Available Tools

| Tool | Description |
|------|-------------|
| list_projects | List all projects |
| get_project | Project details with repo stats |
| browse_code | List files in a directory |
| read_file | Read file content |
| get_timeline | Recent checkins (optional branch filter) |
| get_checkin | Checkin detail with file changes |
| search_code | Search across checkins, tickets, wiki |
| list_tickets | List tickets (optional status filter) |
| get_ticket | Ticket detail with comments |
| create_ticket | Create a new ticket |
| update_ticket | Update ticket status, add comment |
| list_wiki_pages | List all wiki pages |
| get_wiki_page | Read wiki page content |
| list_branches | List all branches |
| get_file_blame | Blame annotations for a file |
| get_file_history | Commit history for a file |
| sql_query | Run read-only SQL against Fossil SQLite |

All tools accept a `slug` parameter to identify the project.

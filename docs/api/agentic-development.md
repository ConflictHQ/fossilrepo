# Agentic Development

FossilRepo is built for AI-assisted development at scale. Traditional Git forges impose rate limits that cripple agent workflows. FossilRepo eliminates these bottlenecks.

## The Problem

AI coding agents make dozens of API calls per task. On GitHub:
- 5,000 API calls/hour limit (agents burn through this in minutes)
- 30 search requests/minute
- Webhook delivery delays
- Actions queue congestion

Multiple agents working in parallel hit rate limits within seconds.

## The Solution

```
AI Agent (Claude Code, Cursor, etc.)
  |
  v
FossilRepo MCP Server / API    <-- zero rate limits
  |
  v
Fossil repos (.fossil SQLite)  <-- local disk, instant
  |
  v (scheduled, batched)
Git Mirror --> GitHub           <-- rate-limit-aware sync
```

Agents work against FossilRepo locally with no limits. Changes sync to GitHub on a schedule. GitHub becomes a downstream mirror, not the bottleneck.

## Connecting AI Tools

### MCP Server (Recommended)

The MCP server gives AI tools native access to all FossilRepo capabilities.

```bash
pip install fossilrepo
fossilrepo-mcp
```

Claude Code config:
```json
{
  "mcpServers": {
    "fossilrepo": {
      "command": "fossilrepo-mcp"
    }
  }
}
```

17 tools available: browse code, read files, search, manage tickets, view timeline/diffs/blame, create tickets, run SQL queries.

### JSON API

For tools without MCP support:
```
curl -H "Authorization: Bearer frp_abc123..." \
  http://localhost:8000/projects/myproject/fossil/api/timeline
```

### Batch API

Reduce round-trips by 25x:
```json
POST /api/batch
{"requests": [
  {"method": "GET", "path": "/api/timeline"},
  {"method": "GET", "path": "/api/tickets", "params": {"status": "Open"}},
  {"method": "GET", "path": "/api/wiki/Home"}
]}
```

## The Agent Workflow

### 1. Discover Work

Browse open, unclaimed tickets:
```
GET /api/tickets/unclaimed
```

### 2. Claim a Ticket

Atomic claiming prevents two agents from working on the same thing:
```
POST /api/tickets/<uuid>/claim
{"agent_id": "claude-session-abc"}
```
Returns 200 if claimed, 409 if already taken by another agent.

### 3. Create an Isolated Workspace

Each agent gets its own Fossil branch and checkout directory:
```
POST /api/workspaces/create
{"name": "fix-auth-bug", "agent_id": "claude-session-abc"}
```

No interference with other agents or the main branch.

### 4. Do the Work

Read code, understand context, make changes, commit. All via MCP tools or API:
```
POST /api/workspaces/fix-auth-bug/commit
{"message": "Fix null check in auth middleware"}
```

### 5. Submit for Review

```
POST /api/reviews/create
{
  "title": "Fix null pointer in auth module",
  "description": "The auth check was failing when...",
  "diff": "--- a/src/auth.py\n+++ b/src/auth.py\n...",
  "workspace": "fix-auth-bug"
}
```

### 6. Review and Merge

Another agent (or human) reviews:
```
POST /api/reviews/<id>/approve
POST /api/reviews/<id>/merge
```

### 7. Release the Claim

```
POST /api/tickets/<uuid>/submit
{"summary": "Fixed by closing the null check gap"}
```

## Real-Time Coordination

### Server-Sent Events

Agents subscribe to a live event stream instead of polling:
```
GET /api/events
```

Events:
- `checkin` — new commits pushed
- `claim` — ticket claimed or released
- `workspace` — workspace created, merged, or abandoned

### Multi-Agent Safety

FossilRepo prevents agent collisions through:

1. **Atomic ticket claiming** — database-level locking via `select_for_update`. Only one agent can claim a ticket.
2. **Isolated workspaces** — each agent works on its own Fossil branch in its own checkout directory. No merge conflicts during work.
3. **Code review gate** — changes must be reviewed (by human or another agent) before merging to trunk.
4. **Branch protection** — protected branches block non-admin pushes. CI status checks can be required.
5. **SSE events** — agents know what others are doing in real-time, avoiding duplicate work.

## Comparison with GitHub

| Feature | GitHub | FossilRepo |
|---------|--------|------------|
| API rate limit | 5,000/hour | Unlimited |
| Search rate limit | 30/min | Unlimited |
| Agent workspace | Shared branch | Isolated checkout |
| Task claiming | None (race conditions) | Atomic (DB-locked) |
| Batch API | None | 25 calls/request |
| Real-time events | Webhooks (delayed) | SSE (instant) |
| Code review | Pull request (heavyweight) | Lightweight API |
| MCP support | No | 17 tools |
| CI status API | Rate-limited | Unlimited |
| Self-hosted | No | Yes |
| Cost | Per-seat | Free (MIT) |

## Why Fossil for Agents?

Fossil's architecture is uniquely suited for agentic development:

- **Single-file repos** — each `.fossil` file is a complete SQLite database. No complex storage, no git pack files, no network dependencies.
- **Built-in everything** — tickets, wiki, forum, technotes all in one file. Agents manage the full development lifecycle without switching tools.
- **SQLite = instant reads** — FossilReader opens the file directly. No API calls, no HTTP, no rate limits. Microsecond latency.
- **Offline-first** — works without internet. Sync to GitHub when ready, on your schedule.
- **Clone = complete backup** — `fossil clone` gives you everything: code, tickets, wiki, forum. One file, one copy.
- **Branching without overhead** — Fossil branches are lightweight metadata, not separate directory trees. Creating 50 agent workspaces costs nothing.

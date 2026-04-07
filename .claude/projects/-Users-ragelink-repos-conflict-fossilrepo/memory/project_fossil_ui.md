---
name: Fossilrepo project status
description: Current state of the fossilrepo project — features built, architecture decisions, what's shipped
type: project
---

Fossilrepo is feature-complete for v0.1.0 open source release as of 2026-04-07.

**Architecture:** Django 5 + HTMX + Alpine.js + Tailwind CSS wrapping Fossil SCM. Reads .fossil SQLite files directly via FossilReader, writes via FossilCLI subprocess. No persistent Fossil HTTP server — uses `fossil http` CGI mode for sync.

**Key decisions:**
- Fossil is primary, Git is secondary (downstream mirror)
- Single org model (no multi-org)
- Django-backed forum posts supplement Fossil's native forum (doesn't sync via clone)
- Chat deferred (wontfix) — multiple approaches possible
- Fossil HTTP server proxy deferred (wontfix) — CGI mode handles sync
- SSH keys encrypted at rest (Fernet/AES-128-CBC)
- Branch protection enforcement at xfer proxy level (non-admins get read-only when protected branches exist)

**Agentic development platform:**
- MCP server (17 tools) for AI tool integration
- Batch API (25 calls/request), Agent Workspaces (isolated branches)
- Task claiming (atomic), SSE events, Code Review API
- JSON API (10+ endpoints) with Bearer token auth
- Zero rate limits — all local

**117 checkins in Fossil, 31 tickets (29 Fixed, 2 Wontfix, 0 Open), 5 wiki pages, 500+ tests.**

**How to apply:** This is the reference for what's built. Check before suggesting features that already exist.

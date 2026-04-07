"""Tool definitions and handlers for the fossilrepo MCP server.

Each tool maps to a Fossil repository operation -- reads go through
FossilReader (direct SQLite), writes go through FossilCLI (fossil binary).
"""

from mcp.types import Tool

TOOLS = [
    Tool(
        name="list_projects",
        description="List all projects in the fossilrepo instance",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_project",
        description="Get details about a specific project including repo stats",
        inputSchema={
            "type": "object",
            "properties": {"slug": {"type": "string", "description": "Project slug"}},
            "required": ["slug"],
        },
    ),
    Tool(
        name="browse_code",
        description="List files in a directory of a project's repository",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "path": {"type": "string", "description": "Directory path (empty for root)", "default": ""},
            },
            "required": ["slug"],
        },
    ),
    Tool(
        name="read_file",
        description="Read the content of a file from a project's repository",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "filepath": {"type": "string", "description": "File path in the repo"},
            },
            "required": ["slug", "filepath"],
        },
    ),
    Tool(
        name="get_timeline",
        description="Get recent checkins/commits for a project",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "limit": {"type": "integer", "description": "Number of entries", "default": 25},
                "branch": {"type": "string", "description": "Filter by branch", "default": ""},
            },
            "required": ["slug"],
        },
    ),
    Tool(
        name="get_checkin",
        description="Get details of a specific checkin including file changes",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "uuid": {"type": "string", "description": "Checkin UUID (or prefix)"},
            },
            "required": ["slug", "uuid"],
        },
    ),
    Tool(
        name="search_code",
        description="Search across checkins, tickets, and wiki pages",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "default": 25},
            },
            "required": ["slug", "query"],
        },
    ),
    Tool(
        name="list_tickets",
        description="List tickets for a project with optional status filter",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "status": {"type": "string", "description": "Filter by status (Open, Fixed, Closed)", "default": ""},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["slug"],
        },
    ),
    Tool(
        name="get_ticket",
        description="Get ticket details including comments",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "uuid": {"type": "string", "description": "Ticket UUID (or prefix)"},
            },
            "required": ["slug", "uuid"],
        },
    ),
    Tool(
        name="create_ticket",
        description="Create a new ticket in a project",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "Ticket description"},
                "type": {"type": "string", "default": "Code_Defect"},
                "severity": {"type": "string", "default": "Important"},
                "priority": {"type": "string", "default": "Medium"},
            },
            "required": ["slug", "title", "body"],
        },
    ),
    Tool(
        name="update_ticket",
        description="Update a ticket's status, add a comment",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "uuid": {"type": "string", "description": "Ticket UUID"},
                "status": {"type": "string", "description": "New status", "default": ""},
                "comment": {"type": "string", "description": "Comment to add", "default": ""},
            },
            "required": ["slug", "uuid"],
        },
    ),
    Tool(
        name="list_wiki_pages",
        description="List all wiki pages in a project",
        inputSchema={
            "type": "object",
            "properties": {"slug": {"type": "string", "description": "Project slug"}},
            "required": ["slug"],
        },
    ),
    Tool(
        name="get_wiki_page",
        description="Read a wiki page's content",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "page_name": {"type": "string", "description": "Wiki page name"},
            },
            "required": ["slug", "page_name"],
        },
    ),
    Tool(
        name="list_branches",
        description="List all branches in a project's repository",
        inputSchema={
            "type": "object",
            "properties": {"slug": {"type": "string", "description": "Project slug"}},
            "required": ["slug"],
        },
    ),
    Tool(
        name="get_file_blame",
        description="Get blame annotations for a file showing who changed each line",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "filepath": {"type": "string", "description": "File path"},
            },
            "required": ["slug", "filepath"],
        },
    ),
    Tool(
        name="get_file_history",
        description="Get commit history for a specific file",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "filepath": {"type": "string", "description": "File path"},
                "limit": {"type": "integer", "default": 25},
            },
            "required": ["slug", "filepath"],
        },
    ),
    Tool(
        name="sql_query",
        description="Run a read-only SQL query against the Fossil SQLite database. Only SELECT allowed.",
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Project slug"},
                "sql": {"type": "string", "description": "SQL query (SELECT only)"},
            },
            "required": ["slug", "sql"],
        },
    ),
]


def _isoformat(dt):
    """Safely format a datetime to ISO 8601, or None."""
    if dt is None:
        return None
    return dt.isoformat()


def _get_repo(slug):
    """Look up project and its FossilRepository by slug.

    Raises Project.DoesNotExist or FossilRepository.DoesNotExist on miss.
    """
    from fossil.models import FossilRepository
    from projects.models import Project

    project = Project.objects.get(slug=slug, deleted_at__isnull=True)
    repo = FossilRepository.objects.get(project=project, deleted_at__isnull=True)
    return project, repo


def execute_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tool call to the appropriate handler."""
    handlers = {
        "list_projects": _list_projects,
        "get_project": _get_project,
        "browse_code": _browse_code,
        "read_file": _read_file,
        "get_timeline": _get_timeline,
        "get_checkin": _get_checkin,
        "search_code": _search_code,
        "list_tickets": _list_tickets,
        "get_ticket": _get_ticket,
        "create_ticket": _create_ticket,
        "update_ticket": _update_ticket,
        "list_wiki_pages": _list_wiki_pages,
        "get_wiki_page": _get_wiki_page,
        "list_branches": _list_branches,
        "get_file_blame": _get_file_blame,
        "get_file_history": _get_file_history,
        "sql_query": _sql_query,
    }
    handler = handlers.get(name)
    if not handler:
        return {"error": f"Unknown tool: {name}"}
    try:
        return handler(arguments)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Read-only handlers (FossilReader)
# ---------------------------------------------------------------------------


def _list_projects(args):
    from projects.models import Project

    projects = Project.objects.filter(deleted_at__isnull=True)
    return {
        "projects": [
            {
                "name": p.name,
                "slug": p.slug,
                "description": p.description or "",
                "visibility": p.visibility,
            }
            for p in projects
        ]
    }


def _get_project(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])
    result = {
        "name": project.name,
        "slug": project.slug,
        "description": project.description or "",
        "visibility": project.visibility,
        "star_count": project.star_count,
        "filename": repo.filename,
        "file_size_bytes": repo.file_size_bytes,
        "checkin_count": repo.checkin_count,
        "last_checkin_at": _isoformat(repo.last_checkin_at),
    }
    if repo.exists_on_disk:
        with FossilReader(repo.full_path) as reader:
            meta = reader.get_metadata()
            result["fossil_project_name"] = meta.project_name
            result["fossil_checkin_count"] = meta.checkin_count
            result["fossil_ticket_count"] = meta.ticket_count
            result["fossil_wiki_page_count"] = meta.wiki_page_count
    return result


def _browse_code(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])
    path = args.get("path", "")

    with FossilReader(repo.full_path) as reader:
        checkin = reader.get_latest_checkin_uuid()
        if not checkin:
            return {"files": [], "error": "No checkins in repository"}

        files = reader.get_files_at_checkin(checkin)

        # Filter to requested directory
        if path:
            path = path.rstrip("/") + "/"
            files = [f for f in files if f.name.startswith(path)]

        return {
            "checkin": checkin,
            "path": path,
            "files": [
                {
                    "name": f.name,
                    "uuid": f.uuid,
                    "size": f.size,
                    "last_commit_message": f.last_commit_message,
                    "last_commit_user": f.last_commit_user,
                    "last_commit_time": _isoformat(f.last_commit_time),
                }
                for f in files
            ],
        }


def _read_file(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])

    with FossilReader(repo.full_path) as reader:
        checkin = reader.get_latest_checkin_uuid()
        if not checkin:
            return {"error": "No checkins in repository"}

        files = reader.get_files_at_checkin(checkin)
        target = args["filepath"]

        for f in files:
            if f.name == target:
                content = reader.get_file_content(f.uuid)
                if isinstance(content, bytes):
                    try:
                        content = content.decode("utf-8")
                    except UnicodeDecodeError:
                        return {"filepath": target, "binary": True, "size": len(content)}
                return {"filepath": target, "content": content}

        return {"error": f"File not found: {target}"}


def _get_timeline(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])
    limit = args.get("limit", 25)
    branch_filter = args.get("branch", "")

    with FossilReader(repo.full_path) as reader:
        entries = reader.get_timeline(limit=limit, event_type="ci")

    checkins = []
    for e in entries:
        entry = {
            "uuid": e.uuid,
            "timestamp": _isoformat(e.timestamp),
            "user": e.user,
            "comment": e.comment,
            "branch": e.branch,
        }
        checkins.append(entry)

    if branch_filter:
        checkins = [c for c in checkins if c["branch"] == branch_filter]

    return {"checkins": checkins, "total": len(checkins)}


def _get_checkin(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])

    with FossilReader(repo.full_path) as reader:
        detail = reader.get_checkin_detail(args["uuid"])

    if detail is None:
        return {"error": "Checkin not found"}

    return {
        "uuid": detail.uuid,
        "timestamp": _isoformat(detail.timestamp),
        "user": detail.user,
        "comment": detail.comment,
        "branch": detail.branch,
        "parent_uuid": detail.parent_uuid,
        "is_merge": detail.is_merge,
        "files_changed": detail.files_changed,
    }


def _search_code(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])
    query = args["query"]
    limit = args.get("limit", 25)

    with FossilReader(repo.full_path) as reader:
        results = reader.search(query, limit=limit)

    # Serialize datetimes in results
    for checkin in results.get("checkins", []):
        checkin["timestamp"] = _isoformat(checkin.get("timestamp"))
    for ticket in results.get("tickets", []):
        ticket["created"] = _isoformat(ticket.get("created"))

    return results


def _list_tickets(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])
    status_filter = args.get("status", "") or None
    limit = args.get("limit", 50)

    with FossilReader(repo.full_path) as reader:
        tickets = reader.get_tickets(status=status_filter, limit=limit)

    return {
        "tickets": [
            {
                "uuid": t.uuid,
                "title": t.title,
                "status": t.status,
                "type": t.type,
                "subsystem": t.subsystem,
                "priority": t.priority,
                "created": _isoformat(t.created),
            }
            for t in tickets
        ],
        "total": len(tickets),
    }


def _get_ticket(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])

    with FossilReader(repo.full_path) as reader:
        ticket = reader.get_ticket_detail(args["uuid"])
        if ticket is None:
            return {"error": "Ticket not found"}
        comments = reader.get_ticket_comments(args["uuid"])

    return {
        "uuid": ticket.uuid,
        "title": ticket.title,
        "status": ticket.status,
        "type": ticket.type,
        "subsystem": ticket.subsystem,
        "priority": ticket.priority,
        "severity": ticket.severity,
        "resolution": ticket.resolution,
        "body": ticket.body,
        "created": _isoformat(ticket.created),
        "comments": [
            {
                "timestamp": _isoformat(c.get("timestamp")),
                "user": c.get("user", ""),
                "comment": c.get("comment", ""),
                "mimetype": c.get("mimetype", "text/plain"),
            }
            for c in comments
        ],
    }


# ---------------------------------------------------------------------------
# Write handlers (FossilCLI)
# ---------------------------------------------------------------------------


def _create_ticket(args):
    from fossil.cli import FossilCLI

    project, repo = _get_repo(args["slug"])

    cli = FossilCLI()
    cli.ensure_default_user(repo.full_path)

    fields = {
        "title": args["title"],
        "comment": args["body"],
        "type": args.get("type", "Code_Defect"),
        "severity": args.get("severity", "Important"),
        "priority": args.get("priority", "Medium"),
        "status": "Open",
    }

    success = cli.ticket_add(repo.full_path, fields)
    if not success:
        return {"error": "Failed to create ticket"}

    return {"success": True, "title": args["title"]}


def _update_ticket(args):
    from fossil.cli import FossilCLI

    project, repo = _get_repo(args["slug"])

    cli = FossilCLI()
    cli.ensure_default_user(repo.full_path)

    fields = {}
    if args.get("status"):
        fields["status"] = args["status"]
    if args.get("comment"):
        fields["icomment"] = args["comment"]

    if not fields:
        return {"error": "No fields to update (provide status or comment)"}

    success = cli.ticket_change(repo.full_path, args["uuid"], fields)
    if not success:
        return {"error": "Failed to update ticket"}

    return {"success": True, "uuid": args["uuid"]}


# ---------------------------------------------------------------------------
# Wiki handlers (FossilReader for reads)
# ---------------------------------------------------------------------------


def _list_wiki_pages(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])

    with FossilReader(repo.full_path) as reader:
        pages = reader.get_wiki_pages()

    return {
        "pages": [
            {
                "name": p.name,
                "last_modified": _isoformat(p.last_modified),
                "user": p.user,
            }
            for p in pages
        ]
    }


def _get_wiki_page(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])

    with FossilReader(repo.full_path) as reader:
        page = reader.get_wiki_page(args["page_name"])

    if page is None:
        return {"error": f"Wiki page not found: {args['page_name']}"}

    return {
        "name": page.name,
        "content": page.content,
        "last_modified": _isoformat(page.last_modified),
        "user": page.user,
    }


# ---------------------------------------------------------------------------
# Branch and file history handlers
# ---------------------------------------------------------------------------


def _list_branches(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])

    with FossilReader(repo.full_path) as reader:
        branches = reader.get_branches()

    return {
        "branches": [
            {
                "name": b["name"],
                "last_checkin": _isoformat(b["last_checkin"]),
                "last_user": b["last_user"],
                "checkin_count": b["checkin_count"],
                "last_uuid": b["last_uuid"],
            }
            for b in branches
        ]
    }


def _get_file_blame(args):
    from fossil.cli import FossilCLI

    project, repo = _get_repo(args["slug"])

    cli = FossilCLI()
    lines = cli.blame(repo.full_path, args["filepath"])
    return {"filepath": args["filepath"], "lines": lines, "total": len(lines)}


def _get_file_history(args):
    from fossil.reader import FossilReader

    project, repo = _get_repo(args["slug"])
    limit = args.get("limit", 25)

    with FossilReader(repo.full_path) as reader:
        history = reader.get_file_history(args["filepath"], limit=limit)

    for entry in history:
        entry["timestamp"] = _isoformat(entry.get("timestamp"))

    return {"filepath": args["filepath"], "history": history, "total": len(history)}


# ---------------------------------------------------------------------------
# SQL query handler
# ---------------------------------------------------------------------------


def _sql_query(args):
    from fossil.reader import FossilReader
    from fossil.ticket_reports import TicketReport

    sql = args["sql"]
    error = TicketReport.validate_sql(sql)
    if error:
        return {"error": error}

    project, repo = _get_repo(args["slug"])

    with FossilReader(repo.full_path) as reader:
        cursor = reader.conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(500)
        return {
            "columns": columns,
            "rows": [list(row) for row in rows],
            "count": len(rows),
        }

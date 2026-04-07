"""JSON API endpoints for programmatic access to Fossil repositories.

All endpoints live under /projects/<slug>/fossil/api/.
Auth: Bearer token (APIToken or PersonalAccessToken) or session cookie.
All responses are JSON. All read endpoints check can_read_project.
"""

import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
import time

from django.db import transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.test import RequestFactory
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from fossil.api_auth import authenticate_request
from fossil.models import FossilRepository
from fossil.reader import FossilReader
from projects.access import can_admin_project, can_read_project, can_write_project
from projects.models import Project

logger = logging.getLogger(__name__)


def _get_repo(slug):
    """Look up project and repository by slug, or return 404 JSON."""
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    repo = get_object_or_404(FossilRepository, project=project, deleted_at__isnull=True)
    return project, repo


def _check_api_auth(request, project, repo, required_scope="read"):
    """Authenticate request and check access.

    Args:
        required_scope: "read" or "write" — enforced on both API tokens and PAT scopes.

    Returns (user, token, error_response). If error_response is not None,
    the caller should return it immediately.
    """
    user, token, err = authenticate_request(request, repository=repo, required_scope=required_scope)
    if err is not None:
        return None, None, err

    # For project-scoped APITokens (no user), the token itself grants access
    # since it's scoped to this repository and scope was already checked.
    if token is not None and user is None:
        return user, token, None

    # For user-scoped auth (PAT or session), check project visibility
    if user is not None:
        if required_scope == "write" and not can_write_project(user, project):
            return None, None, JsonResponse({"error": "Write access required"}, status=403)
        if not can_read_project(user, project):
            return None, None, JsonResponse({"error": "Access denied"}, status=403)

    return user, token, None


def _paginate_params(request, default_per_page=25, max_per_page=100):
    """Extract and validate page/per_page from query params."""
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = min(max_per_page, max(1, int(request.GET.get("per_page", str(default_per_page)))))
    except (ValueError, TypeError):
        per_page = default_per_page
    return page, per_page


def _isoformat(dt):
    """Safely format a datetime to ISO 8601, or None."""
    if dt is None:
        return None
    return dt.isoformat()


# --- API Documentation ---


@csrf_exempt
@require_GET
def api_docs(request, slug):
    """Return JSON listing all available API endpoints with descriptions."""
    base = f"/projects/{slug}/fossil/api"
    return JsonResponse(
        {
            "endpoints": [
                {"method": "GET", "path": f"{base}/project", "description": "Project metadata"},
                {
                    "method": "GET",
                    "path": f"{base}/timeline",
                    "description": "Recent checkins (paginated)",
                    "params": "page, per_page, branch",
                },
                {
                    "method": "GET",
                    "path": f"{base}/tickets",
                    "description": "Ticket list (paginated, filterable)",
                    "params": "page, per_page, status",
                },
                {"method": "GET", "path": f"{base}/tickets/<uuid>", "description": "Single ticket detail with comments"},
                {"method": "GET", "path": f"{base}/wiki", "description": "Wiki page list"},
                {"method": "GET", "path": f"{base}/wiki/<name>", "description": "Single wiki page with content"},
                {"method": "GET", "path": f"{base}/branches", "description": "Branch list"},
                {"method": "GET", "path": f"{base}/tags", "description": "Tag list"},
                {"method": "GET", "path": f"{base}/releases", "description": "Release list"},
                {"method": "GET", "path": f"{base}/search", "description": "Search across checkins, tickets, wiki", "params": "q"},
                {
                    "method": "POST",
                    "path": f"{base}/batch",
                    "description": "Execute multiple API calls in a single request (max 25)",
                    "body": '{"requests": [{"method": "GET", "path": "/api/timeline", "params": {}}]}',
                },
                {"method": "GET", "path": f"{base}/workspaces", "description": "List agent workspaces", "params": "status"},
                {
                    "method": "POST",
                    "path": f"{base}/workspaces/create",
                    "description": "Create an isolated agent workspace",
                    "body": '{"name": "...", "description": "...", "agent_id": "..."}',
                },
                {"method": "GET", "path": f"{base}/workspaces/<name>", "description": "Get workspace details"},
                {
                    "method": "POST",
                    "path": f"{base}/workspaces/<name>/commit",
                    "description": "Commit changes in a workspace",
                    "body": '{"message": "...", "files": []}',
                },
                {
                    "method": "POST",
                    "path": f"{base}/workspaces/<name>/merge",
                    "description": "Merge workspace branch back to trunk",
                    "body": '{"target_branch": "trunk"}',
                },
                {
                    "method": "DELETE",
                    "path": f"{base}/workspaces/<name>/abandon",
                    "description": "Abandon and clean up a workspace",
                },
                {
                    "method": "POST",
                    "path": f"{base}/tickets/<uuid>/claim",
                    "description": "Claim a ticket for exclusive agent work",
                    "body": '{"agent_id": "...", "workspace": "..."}',
                },
                {
                    "method": "POST",
                    "path": f"{base}/tickets/<uuid>/release",
                    "description": "Release a ticket claim",
                },
                {
                    "method": "POST",
                    "path": f"{base}/tickets/<uuid>/submit",
                    "description": "Submit completed work for a claimed ticket",
                    "body": '{"summary": "...", "files_changed": [...]}',
                },
                {
                    "method": "GET",
                    "path": f"{base}/tickets/unclaimed",
                    "description": "List tickets not claimed by any agent",
                    "params": "status, limit",
                },
                {"method": "GET", "path": f"{base}/events", "description": "Server-Sent Events stream for real-time events"},
                {
                    "method": "POST",
                    "path": f"{base}/reviews/create",
                    "description": "Submit code changes for review",
                    "body": '{"title": "...", "diff": "...", "files_changed": [...], "agent_id": "..."}',
                },
                {
                    "method": "GET",
                    "path": f"{base}/reviews",
                    "description": "List code reviews",
                    "params": "status, page, per_page",
                },
                {"method": "GET", "path": f"{base}/reviews/<id>", "description": "Get review with comments"},
                {
                    "method": "POST",
                    "path": f"{base}/reviews/<id>/comment",
                    "description": "Add a comment to a review",
                    "body": '{"body": "...", "file_path": "...", "line_number": 42, "author": "..."}',
                },
                {"method": "POST", "path": f"{base}/reviews/<id>/approve", "description": "Approve a review"},
                {"method": "POST", "path": f"{base}/reviews/<id>/request-changes", "description": "Request changes on a review"},
                {"method": "POST", "path": f"{base}/reviews/<id>/merge", "description": "Merge an approved review"},
            ],
            "auth": "Bearer token (Authorization: Bearer <token>) or session cookie",
        }
    )


# --- Project Metadata ---


@csrf_exempt
@require_GET
def api_project(request, slug):
    """Return project metadata as JSON."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    return JsonResponse(
        {
            "name": project.name,
            "slug": project.slug,
            "description": project.description or "",
            "visibility": project.visibility,
            "star_count": project.star_count,
        }
    )


# --- Timeline ---


@csrf_exempt
@require_GET
def api_timeline(request, slug):
    """Return recent checkins as JSON, paginated."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    page, per_page = _paginate_params(request)
    branch_filter = request.GET.get("branch", "").strip()
    offset = (page - 1) * per_page

    reader = FossilReader(repo.full_path)
    with reader:
        entries = reader.get_timeline(limit=per_page, offset=offset, event_type="ci")
        total = reader.get_checkin_count()

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

    # If branch filter is set, filter in Python (Fossil's timeline query
    # doesn't support branch filtering at the SQL level without extra joins).
    if branch_filter:
        checkins = [c for c in checkins if c["branch"] == branch_filter]

    total_pages = max(1, math.ceil(total / per_page))

    return JsonResponse(
        {
            "checkins": checkins,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }
    )


# --- Tickets ---


@csrf_exempt
@require_GET
def api_tickets(request, slug):
    """Return ticket list as JSON, paginated and filterable by status."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    page, per_page = _paginate_params(request)
    status_filter = request.GET.get("status", "").strip() or None

    reader = FossilReader(repo.full_path)
    with reader:
        all_tickets = reader.get_tickets(status=status_filter, limit=1000)

    total = len(all_tickets)
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    page_tickets = all_tickets[(page - 1) * per_page : page * per_page]

    tickets = []
    for t in page_tickets:
        tickets.append(
            {
                "uuid": t.uuid,
                "title": t.title,
                "status": t.status,
                "type": t.type,
                "subsystem": t.subsystem,
                "priority": t.priority,
                "severity": t.severity,
                "created": _isoformat(t.created),
            }
        )

    return JsonResponse(
        {
            "tickets": tickets,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }
    )


@csrf_exempt
@require_GET
def api_ticket_detail(request, slug, ticket_uuid):
    """Return a single ticket with its comments as JSON."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    reader = FossilReader(repo.full_path)
    with reader:
        ticket = reader.get_ticket_detail(ticket_uuid)
        if ticket is None:
            return JsonResponse({"error": "Ticket not found"}, status=404)
        comments = reader.get_ticket_comments(ticket_uuid)

    comment_list = []
    for c in comments:
        comment_list.append(
            {
                "timestamp": _isoformat(c.get("timestamp")),
                "user": c.get("user", ""),
                "comment": c.get("comment", ""),
                "mimetype": c.get("mimetype", "text/plain"),
            }
        )

    return JsonResponse(
        {
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
            "comments": comment_list,
        }
    )


# --- Wiki ---


@csrf_exempt
@require_GET
def api_wiki_list(request, slug):
    """Return list of wiki pages as JSON."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    reader = FossilReader(repo.full_path)
    with reader:
        pages = reader.get_wiki_pages()

    page_list = []
    for p in pages:
        page_list.append(
            {
                "name": p.name,
                "last_modified": _isoformat(p.last_modified),
                "user": p.user,
            }
        )

    return JsonResponse({"pages": page_list})


@csrf_exempt
@require_GET
def api_wiki_page(request, slug, page_name):
    """Return a single wiki page with its content as JSON."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    reader = FossilReader(repo.full_path)
    with reader:
        page = reader.get_wiki_page(page_name)

    if page is None:
        return JsonResponse({"error": "Wiki page not found"}, status=404)

    # Render content to HTML for convenience
    from fossil.views import _render_fossil_content

    content_html = _render_fossil_content(page.content, project_slug=slug) if page.content else ""

    return JsonResponse(
        {
            "name": page.name,
            "content": page.content,
            "content_html": content_html,
            "last_modified": _isoformat(page.last_modified),
            "user": page.user,
        }
    )


# --- Branches ---


@csrf_exempt
@require_GET
def api_branches(request, slug):
    """Return list of branches as JSON."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    reader = FossilReader(repo.full_path)
    with reader:
        branches = reader.get_branches()

    branch_list = []
    for b in branches:
        branch_list.append(
            {
                "name": b["name"],
                "last_checkin": _isoformat(b["last_checkin"]),
                "last_user": b["last_user"],
                "checkin_count": b["checkin_count"],
                "last_uuid": b["last_uuid"],
            }
        )

    return JsonResponse({"branches": branch_list})


# --- Tags ---


@csrf_exempt
@require_GET
def api_tags(request, slug):
    """Return list of tags as JSON."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    reader = FossilReader(repo.full_path)
    with reader:
        tags = reader.get_tags()

    tag_list = []
    for t in tags:
        tag_list.append(
            {
                "name": t["name"],
                "timestamp": _isoformat(t["timestamp"]),
                "user": t["user"],
                "uuid": t["uuid"],
            }
        )

    return JsonResponse({"tags": tag_list})


# --- Releases ---


@csrf_exempt
@require_GET
def api_releases(request, slug):
    """Return list of releases as JSON. Drafts excluded for non-writers."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    from fossil.releases import Release
    from projects.access import can_write_project

    releases_qs = Release.objects.filter(repository=repo, deleted_at__isnull=True)

    # Only show drafts to users with write access
    has_write = False
    if user is not None:
        has_write = can_write_project(user, project)
    if not has_write:
        releases_qs = releases_qs.filter(is_draft=False)

    release_list = []
    for r in releases_qs:
        assets = []
        for a in r.assets.filter(deleted_at__isnull=True):
            assets.append(
                {
                    "name": a.name,
                    "file_size_bytes": a.file_size_bytes,
                    "content_type": a.content_type,
                    "download_count": a.download_count,
                }
            )
        release_list.append(
            {
                "tag_name": r.tag_name,
                "name": r.name,
                "body": r.body,
                "is_prerelease": r.is_prerelease,
                "is_draft": r.is_draft,
                "published_at": _isoformat(r.published_at),
                "checkin_uuid": r.checkin_uuid,
                "assets": assets,
            }
        )

    return JsonResponse({"releases": release_list})


# --- Search ---


@csrf_exempt
@require_GET
def api_search(request, slug):
    """Search across checkins, tickets, and wiki pages. Returns JSON."""
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"error": "Query parameter 'q' is required"}, status=400)

    reader = FossilReader(repo.full_path)
    with reader:
        results = reader.search(query, limit=50)

    # Serialize datetimes in results
    for checkin in results.get("checkins", []):
        checkin["timestamp"] = _isoformat(checkin.get("timestamp"))
    for ticket in results.get("tickets", []):
        ticket["created"] = _isoformat(ticket.get("created"))

    return JsonResponse(results)


# --- Batch API ---

# Map API paths to (view_function, extra_path_regex_or_None).
# Entries with a regex capture group extract path params (e.g. ticket uuid, wiki page name).
_BATCH_STATIC_ROUTES = {
    "/api/project": api_project,
    "/api/timeline": api_timeline,
    "/api/tickets": api_tickets,
    "/api/wiki": api_wiki_list,
    "/api/branches": api_branches,
    "/api/tags": api_tags,
    "/api/releases": api_releases,
    "/api/search": api_search,
}

_BATCH_DYNAMIC_ROUTES = [
    (re.compile(r"^/api/tickets/([0-9a-fA-F-]+)$"), api_ticket_detail, "ticket_uuid"),
    (re.compile(r"^/api/wiki/(.+)$"), api_wiki_page, "page_name"),
]

_BATCH_MAX_REQUESTS = 25


def _resolve_batch_route(path):
    """Resolve a batch sub-request path to (view_func, kwargs) or (None, None)."""
    view_func = _BATCH_STATIC_ROUTES.get(path)
    if view_func is not None:
        return view_func, {}

    for pattern, view_func, kwarg_name in _BATCH_DYNAMIC_ROUTES:
        m = pattern.match(path)
        if m:
            return view_func, {kwarg_name: m.group(1)}

    return None, None


@csrf_exempt
def api_batch(request, slug):
    """Execute multiple API calls in a single request.

    POST /projects/<slug>/fossil/api/batch
    {
        "requests": [
            {"method": "GET", "path": "/api/timeline", "params": {"per_page": 5}},
            {"method": "GET", "path": "/api/tickets", "params": {"status": "Open"}},
            {"method": "GET", "path": "/api/wiki/Home"}
        ]
    }

    Returns:
    {
        "responses": [
            {"status": 200, "body": {...}},
            {"status": 200, "body": {...}},
            {"status": 200, "body": {...}}
        ]
    }

    Auth: same as other API endpoints (Bearer token or session).
    Limit: 25 sub-requests per batch.
    Only GET sub-requests are supported.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    # Auth check -- same as every other API endpoint
    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    requests_list = body.get("requests")
    if not isinstance(requests_list, list):
        return JsonResponse({"error": "'requests' must be a list"}, status=400)

    if len(requests_list) > _BATCH_MAX_REQUESTS:
        return JsonResponse({"error": f"Maximum {_BATCH_MAX_REQUESTS} requests per batch"}, status=400)

    if len(requests_list) == 0:
        return JsonResponse({"responses": []})

    factory = RequestFactory()
    responses = []

    for sub in requests_list:
        if not isinstance(sub, dict):
            responses.append({"status": 400, "body": {"error": "Each request must be an object"}})
            continue

        method = (sub.get("method") or "GET").upper()
        path = sub.get("path", "")
        params = sub.get("params") or {}

        if method != "GET":
            responses.append({"status": 405, "body": {"error": "Only GET is supported in batch requests"}})
            continue

        if not path:
            responses.append({"status": 400, "body": {"error": "Missing 'path'"}})
            continue

        view_func, extra_kwargs = _resolve_batch_route(path)
        if view_func is None:
            responses.append({"status": 404, "body": {"error": f"Unknown API path: {path}"}})
            continue

        # Build a synthetic GET request preserving auth from the outer request
        full_path = f"/projects/{slug}/fossil{path}"
        synthetic = factory.get(full_path, data=params)

        # Carry over auth state so sub-requests don't re-authenticate
        synthetic.user = request.user
        synthetic.session = request.session
        if "HTTP_AUTHORIZATION" in request.META:
            synthetic.META["HTTP_AUTHORIZATION"] = request.META["HTTP_AUTHORIZATION"]

        try:
            sub_response = view_func(synthetic, slug=slug, **extra_kwargs)
            try:
                response_body = json.loads(sub_response.content)
            except (json.JSONDecodeError, ValueError):
                response_body = {"raw": sub_response.content.decode("utf-8", errors="replace")}
            responses.append({"status": sub_response.status_code, "body": response_body})
        except Exception:
            logger.exception("Batch sub-request failed: %s %s", method, path)
            responses.append({"status": 500, "body": {"error": "Internal error processing sub-request"}})

    return JsonResponse({"responses": responses})


# --- Agent Workspace API ---


def _get_workspace(repo, workspace_name):
    """Look up an active workspace by name, or return 404 JSON."""
    from fossil.workspaces import AgentWorkspace

    workspace = AgentWorkspace.objects.filter(repository=repo, name=workspace_name).first()
    if workspace is None:
        return None
    return workspace


def _check_workspace_ownership(workspace, request, token, data=None):
    """Verify the caller owns this workspace.

    Token-based callers (agents) must provide an agent_id matching the workspace.
    Session-auth users (human operators) are allowed through as human oversight.
    Returns an error JsonResponse if denied, or None if allowed.
    """
    if token is None:
        # Session-auth user — human oversight, allowed
        return None
    if not workspace.agent_id:
        # Workspace has no agent_id — any writer can operate
        return None
    # Token-based caller must supply matching agent_id
    if data is None:
        try:
            data = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, ValueError):
            data = {}
    caller_agent_id = (data.get("agent_id") or "").strip()
    if not caller_agent_id or caller_agent_id != workspace.agent_id:
        return JsonResponse(
            {"error": "Only the owning agent can modify this workspace"},
            status=403,
        )


@csrf_exempt
def api_workspace_list(request, slug):
    """List agent workspaces for a repository.

    GET /projects/<slug>/fossil/api/workspaces
    Optional query params: status (active, merged, abandoned)
    """
    if request.method != "GET":
        return JsonResponse({"error": "GET required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    from fossil.workspaces import AgentWorkspace

    qs = AgentWorkspace.objects.filter(repository=repo)
    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)

    workspaces = []
    for ws in qs:
        workspaces.append(
            {
                "name": ws.name,
                "branch": ws.branch,
                "status": ws.status,
                "agent_id": ws.agent_id,
                "description": ws.description,
                "files_changed": ws.files_changed,
                "commits_made": ws.commits_made,
                "created_at": _isoformat(ws.created_at),
            }
        )

    return JsonResponse({"workspaces": workspaces})


@csrf_exempt
def api_workspace_create(request, slug):
    """Create an isolated agent workspace.

    POST /projects/<slug>/fossil/api/workspaces/create
    {"name": "agent-fix-123", "description": "Fixing bug #123", "agent_id": "claude-abc"}

    Creates a new Fossil branch and checkout directory for the agent.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    # Write access required to create workspaces
    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "Workspace name is required"}, status=400)

    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,198}$", name):
        return JsonResponse(
            {"error": "Invalid workspace name. Use alphanumeric characters, hyphens, dots, and underscores."},
            status=400,
        )

    from fossil.workspaces import AgentWorkspace

    if AgentWorkspace.objects.filter(repository=repo, name=name).exists():
        return JsonResponse({"error": f"Workspace '{name}' already exists"}, status=409)

    branch = f"workspace/{name}"

    # Create workspace checkout directory
    checkout_dir = tempfile.mkdtemp(prefix=f"fossilrepo-ws-{name}-")

    from fossil.cli import FossilCLI

    cli = FossilCLI()

    # Open a checkout in the workspace dir
    result = subprocess.run(
        [cli.binary, "open", str(repo.full_path), "--workdir", checkout_dir],
        capture_output=True,
        text=True,
        timeout=30,
        env=cli._env,
        cwd=checkout_dir,
    )
    if result.returncode != 0:
        shutil.rmtree(checkout_dir, ignore_errors=True)
        return JsonResponse({"error": "Failed to open Fossil checkout", "detail": result.stderr.strip()}, status=500)

    # Create the branch from trunk
    result = subprocess.run(
        [cli.binary, "branch", "new", branch, "trunk"],
        capture_output=True,
        text=True,
        timeout=30,
        env=cli._env,
        cwd=checkout_dir,
    )
    if result.returncode != 0:
        # Clean up on failure
        subprocess.run([cli.binary, "close", "--force"], capture_output=True, cwd=checkout_dir, timeout=10, env=cli._env)
        shutil.rmtree(checkout_dir, ignore_errors=True)
        return JsonResponse({"error": "Failed to create branch", "detail": result.stderr.strip()}, status=500)

    # Switch to the new branch
    result = subprocess.run(
        [cli.binary, "update", branch],
        capture_output=True,
        text=True,
        timeout=30,
        env=cli._env,
        cwd=checkout_dir,
    )
    if result.returncode != 0:
        subprocess.run([cli.binary, "close", "--force"], capture_output=True, cwd=checkout_dir, timeout=10, env=cli._env)
        shutil.rmtree(checkout_dir, ignore_errors=True)
        return JsonResponse({"error": "Failed to switch to branch", "detail": result.stderr.strip()}, status=500)

    workspace = AgentWorkspace.objects.create(
        repository=repo,
        name=name,
        branch=branch,
        agent_id=data.get("agent_id", ""),
        description=data.get("description", ""),
        checkout_path=checkout_dir,
        created_by=user,
    )

    return JsonResponse(
        {
            "name": workspace.name,
            "branch": workspace.branch,
            "status": workspace.status,
            "agent_id": workspace.agent_id,
            "description": workspace.description,
            "created_at": _isoformat(workspace.created_at),
        },
        status=201,
    )


@csrf_exempt
def api_workspace_detail(request, slug, workspace_name):
    """Get details of a specific workspace.

    GET /projects/<slug>/fossil/api/workspaces/<name>
    """
    if request.method != "GET":
        return JsonResponse({"error": "GET required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    workspace = _get_workspace(repo, workspace_name)
    if workspace is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)

    return JsonResponse(
        {
            "name": workspace.name,
            "branch": workspace.branch,
            "status": workspace.status,
            "agent_id": workspace.agent_id,
            "description": workspace.description,
            "files_changed": workspace.files_changed,
            "commits_made": workspace.commits_made,
            "created_at": _isoformat(workspace.created_at),
            "updated_at": _isoformat(workspace.updated_at),
        }
    )


@csrf_exempt
def api_workspace_commit(request, slug, workspace_name):
    """Commit changes in a workspace.

    POST /projects/<slug>/fossil/api/workspaces/<name>/commit
    {"message": "Fix bug #123", "files": ["src/foo.py"]}

    If files is empty or omitted, commits all changed files.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    workspace = _get_workspace(repo, workspace_name)
    if workspace is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)

    if workspace.status != "active":
        return JsonResponse({"error": f"Workspace is {workspace.status}, cannot commit"}, status=409)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    ownership_err = _check_workspace_ownership(workspace, request, token, data)
    if ownership_err is not None:
        return ownership_err

    message = (data.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "Commit message is required"}, status=400)

    files = data.get("files") or []
    checkout_dir = workspace.checkout_path

    from fossil.cli import FossilCLI

    cli = FossilCLI()

    # Add files if specified, otherwise add all changes
    if files:
        for f in files:
            subprocess.run(
                [cli.binary, "add", f],
                capture_output=True,
                text=True,
                timeout=30,
                env=cli._env,
                cwd=checkout_dir,
            )
    else:
        subprocess.run(
            [cli.binary, "addremove"],
            capture_output=True,
            text=True,
            timeout=30,
            env=cli._env,
            cwd=checkout_dir,
        )

    # Commit
    commit_cmd = [cli.binary, "commit", "-m", message, "--no-warnings"]
    result = subprocess.run(
        commit_cmd,
        capture_output=True,
        text=True,
        timeout=60,
        env=cli._env,
        cwd=checkout_dir,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # "nothing has changed" is not really an error
        if "nothing has changed" in stderr.lower() or "nothing has changed" in result.stdout.lower():
            return JsonResponse({"error": "Nothing to commit"}, status=409)
        return JsonResponse({"error": "Commit failed", "detail": stderr}, status=500)

    workspace.commits_made += 1
    workspace.save(update_fields=["commits_made", "updated_at", "version"])

    return JsonResponse(
        {
            "name": workspace.name,
            "branch": workspace.branch,
            "commits_made": workspace.commits_made,
            "message": message,
            "output": result.stdout.strip(),
        }
    )


@csrf_exempt
def api_workspace_merge(request, slug, workspace_name):
    """Merge workspace branch back to trunk.

    POST /projects/<slug>/fossil/api/workspaces/<name>/merge
    {"target_branch": "trunk"}

    Merges the workspace branch into the target branch (default: trunk),
    then closes the workspace checkout and cleans up the directory.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    workspace = _get_workspace(repo, workspace_name)
    if workspace is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)

    if workspace.status != "active":
        return JsonResponse({"error": f"Workspace is {workspace.status}, cannot merge"}, status=409)

    try:
        data = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    ownership_err = _check_workspace_ownership(workspace, request, token, data)
    if ownership_err is not None:
        return ownership_err

    target_branch = (data.get("target_branch") or "trunk").strip()

    # --- Branch protection enforcement ---
    is_admin = user is not None and can_admin_project(user, project)
    if not is_admin:
        from fossil.branch_protection import BranchProtection

        for protection in BranchProtection.objects.filter(repository=repo, deleted_at__isnull=True):
            if protection.matches_branch(target_branch):
                if protection.restrict_push:
                    return JsonResponse(
                        {"error": f"Branch '{target_branch}' is protected: only admins can merge to it"},
                        status=403,
                    )
                if protection.require_status_checks:
                    from fossil.ci import StatusCheck

                    for context in protection.get_required_contexts_list():
                        latest = StatusCheck.objects.filter(repository=repo, context=context).order_by("-created_at").first()
                        if not latest or latest.state != "success":
                            return JsonResponse(
                                {"error": f"Required status check '{context}' has not passed"},
                                status=403,
                            )

    # --- Review gate enforcement ---
    from fossil.code_reviews import CodeReview

    linked_review = CodeReview.objects.filter(repository=repo, workspace=workspace).order_by("-created_at").first()
    if linked_review is not None:
        if linked_review.status != "approved":
            return JsonResponse(
                {"error": f"Linked code review is '{linked_review.status}', must be approved before merging"},
                status=403,
            )
    elif not is_admin:
        # No review exists for this workspace — require one for non-admins
        return JsonResponse(
            {"error": "No approved code review found for this workspace; submit one before merging"},
            status=403,
        )

    from fossil.cli import FossilCLI

    cli = FossilCLI()
    checkout_dir = workspace.checkout_path

    # Switch to target branch
    result = subprocess.run(
        [cli.binary, "update", target_branch],
        capture_output=True,
        text=True,
        timeout=30,
        env=cli._env,
        cwd=checkout_dir,
    )
    if result.returncode != 0:
        return JsonResponse({"error": "Failed to switch to target branch", "detail": result.stderr.strip()}, status=500)

    # Merge workspace branch into target
    result = subprocess.run(
        [cli.binary, "merge", workspace.branch],
        capture_output=True,
        text=True,
        timeout=60,
        env=cli._env,
        cwd=checkout_dir,
    )
    if result.returncode != 0:
        return JsonResponse({"error": "Merge failed", "detail": result.stderr.strip()}, status=500)

    # Commit the merge
    merge_msg = f"Merge {workspace.branch} into {target_branch}"
    commit_result = subprocess.run(
        [cli.binary, "commit", "-m", merge_msg, "--no-warnings"],
        capture_output=True,
        text=True,
        timeout=60,
        env=cli._env,
        cwd=checkout_dir,
    )

    if commit_result.returncode != 0:
        # Merge commit failed — don't close the workspace, let the user retry
        return JsonResponse(
            {"error": "Merge commit failed", "detail": commit_result.stderr.strip()},
            status=500,
        )

    # Close the checkout and clean up (only on successful commit)
    subprocess.run([cli.binary, "close", "--force"], capture_output=True, cwd=checkout_dir, timeout=10, env=cli._env)
    shutil.rmtree(checkout_dir, ignore_errors=True)

    workspace.status = "merged"
    workspace.checkout_path = ""
    workspace.save(update_fields=["status", "checkout_path", "updated_at", "version"])

    # Mark the linked review as merged
    if linked_review is not None and linked_review.status == "approved":
        linked_review.status = "merged"
        linked_review.save(update_fields=["status", "updated_at", "version"])

    return JsonResponse(
        {
            "name": workspace.name,
            "branch": workspace.branch,
            "status": workspace.status,
            "target_branch": target_branch,
            "merge_output": result.stdout.strip(),
            "commit_output": commit_result.stdout.strip() if commit_result.returncode == 0 else "",
        }
    )


@csrf_exempt
def api_workspace_abandon(request, slug, workspace_name):
    """Abandon a workspace, closing the checkout and cleaning up.

    DELETE /projects/<slug>/fossil/api/workspaces/<name>/abandon

    The branch remains in Fossil history but the checkout directory is removed.
    """
    if request.method != "DELETE":
        return JsonResponse({"error": "DELETE required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    workspace = _get_workspace(repo, workspace_name)
    if workspace is None:
        return JsonResponse({"error": "Workspace not found"}, status=404)

    if workspace.status != "active":
        return JsonResponse({"error": f"Workspace is already {workspace.status}"}, status=409)

    ownership_err = _check_workspace_ownership(workspace, request, token)
    if ownership_err is not None:
        return ownership_err

    from fossil.cli import FossilCLI

    cli = FossilCLI()
    checkout_dir = workspace.checkout_path

    # Close checkout and clean up directory
    if checkout_dir:
        subprocess.run([cli.binary, "close", "--force"], capture_output=True, cwd=checkout_dir, timeout=10, env=cli._env)
        shutil.rmtree(checkout_dir, ignore_errors=True)

    workspace.status = "abandoned"
    workspace.checkout_path = ""
    workspace.save(update_fields=["status", "checkout_path", "updated_at", "version"])

    return JsonResponse(
        {
            "name": workspace.name,
            "branch": workspace.branch,
            "status": workspace.status,
        }
    )


# --- Ticket Claiming ---


@csrf_exempt
def api_ticket_claim(request, slug, ticket_uuid):
    """Claim a ticket for exclusive agent work.

    POST /projects/<slug>/fossil/api/tickets/<uuid>/claim
    {"agent_id": "claude-abc", "workspace": "agent-fix-123"}

    Returns 200 if claimed, 409 if already claimed by another agent.
    Uses the unique_together constraint on (repository, ticket_uuid) for atomicity.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    agent_id = (data.get("agent_id") or "").strip()
    if not agent_id:
        return JsonResponse({"error": "agent_id is required"}, status=400)

    # Verify the ticket exists in Fossil
    reader = FossilReader(repo.full_path)
    with reader:
        ticket = reader.get_ticket_detail(ticket_uuid)
    if ticket is None:
        return JsonResponse({"error": "Ticket not found in repository"}, status=404)

    # Resolve optional workspace reference
    workspace_name = (data.get("workspace") or "").strip()
    workspace_obj = None
    if workspace_name:
        from fossil.workspaces import AgentWorkspace

        workspace_obj = AgentWorkspace.objects.filter(repository=repo, name=workspace_name).first()

    from fossil.agent_claims import TicketClaim

    with transaction.atomic():
        # Check for existing active claim (not soft-deleted) with row lock
        existing = TicketClaim.objects.select_for_update().filter(repository=repo, ticket_uuid=ticket_uuid).first()

        if existing:
            if existing.agent_id == agent_id:
                # Idempotent: same agent re-claiming
                return JsonResponse(
                    {
                        "ticket_uuid": existing.ticket_uuid,
                        "agent_id": existing.agent_id,
                        "status": existing.status,
                        "claimed_at": _isoformat(existing.claimed_at),
                        "message": "Already claimed by you",
                    }
                )
            return JsonResponse(
                {
                    "error": "Ticket already claimed",
                    "claimed_by": existing.agent_id,
                    "claimed_at": _isoformat(existing.claimed_at),
                },
                status=409,
            )

        claim = TicketClaim.objects.create(
            repository=repo,
            ticket_uuid=ticket_uuid,
            agent_id=agent_id,
            workspace=workspace_obj,
            created_by=user,
        )

    return JsonResponse(
        {
            "ticket_uuid": claim.ticket_uuid,
            "agent_id": claim.agent_id,
            "status": claim.status,
            "claimed_at": _isoformat(claim.claimed_at),
            "workspace": workspace_name or None,
        },
        status=201,
    )


@csrf_exempt
def api_ticket_release(request, slug, ticket_uuid):
    """Release a ticket claim.

    POST /projects/<slug>/fossil/api/tickets/<uuid>/release
    {"agent_id": "claude-abc"}

    Soft-deletes the claim record so the unique constraint slot is freed.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    try:
        data = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    agent_id = (data.get("agent_id") or "").strip()
    if not agent_id:
        return JsonResponse({"error": "agent_id is required"}, status=400)

    from fossil.agent_claims import TicketClaim

    claim = TicketClaim.objects.filter(repository=repo, ticket_uuid=ticket_uuid).first()
    if claim is None:
        return JsonResponse({"error": "No active claim for this ticket"}, status=404)

    if claim.agent_id != agent_id:
        return JsonResponse({"error": "Only the claiming agent can release this ticket"}, status=403)

    claim.status = "released"
    claim.released_at = timezone.now()
    claim.save(update_fields=["status", "released_at", "updated_at", "version"])
    # Soft-delete to free the unique constraint slot for future claims
    claim.soft_delete(user=user)

    return JsonResponse(
        {
            "ticket_uuid": claim.ticket_uuid,
            "agent_id": claim.agent_id,
            "status": "released",
            "released_at": _isoformat(claim.released_at),
        }
    )


@csrf_exempt
def api_ticket_submit(request, slug, ticket_uuid):
    """Submit completed work for a claimed ticket.

    POST /projects/<slug>/fossil/api/tickets/<uuid>/submit
    {
        "agent_id": "claude-abc",
        "workspace": "agent-fix-123",
        "summary": "Fixed the bug by ...",
        "files_changed": ["src/foo.py", "tests/test_foo.py"]
    }

    Updates the claim status to "submitted" and records the work summary.
    Optionally adds a comment to the Fossil ticket.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    agent_id = (data.get("agent_id") or "").strip()
    if not agent_id:
        return JsonResponse({"error": "agent_id is required"}, status=400)

    from fossil.agent_claims import TicketClaim

    claim = TicketClaim.objects.filter(repository=repo, ticket_uuid=ticket_uuid).first()
    if claim is None:
        return JsonResponse({"error": "No active claim for this ticket"}, status=404)

    if claim.agent_id != agent_id:
        return JsonResponse({"error": "Only the claiming agent can submit work for this ticket"}, status=403)

    if claim.status != "claimed":
        return JsonResponse({"error": f"Claim is already {claim.status}"}, status=409)

    summary = (data.get("summary") or "").strip()
    files_changed = data.get("files_changed") or []

    claim.status = "submitted"
    claim.summary = summary
    claim.files_changed = files_changed
    claim.save(update_fields=["status", "summary", "files_changed", "updated_at", "version"])

    # Optionally add a comment to the Fossil ticket via CLI
    if summary:
        from fossil.cli import FossilCLI

        cli = FossilCLI()
        comment_text = f"[Agent: {claim.agent_id}] Work submitted.\n\n{summary}"
        if files_changed:
            comment_text += f"\n\nFiles changed: {', '.join(files_changed)}"
        cli.ticket_change(repo.full_path, ticket_uuid, {"comment": comment_text})

    return JsonResponse(
        {
            "ticket_uuid": claim.ticket_uuid,
            "agent_id": claim.agent_id,
            "status": claim.status,
            "summary": claim.summary,
            "files_changed": claim.files_changed,
        }
    )


@csrf_exempt
def api_tickets_unclaimed(request, slug):
    """List open tickets that aren't claimed by any agent.

    GET /projects/<slug>/fossil/api/tickets/unclaimed
    Optional query params: status (default: Open), limit (default: 50)
    """
    if request.method != "GET":
        return JsonResponse({"error": "GET required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    status_filter = request.GET.get("status", "Open").strip()
    try:
        limit = min(200, max(1, int(request.GET.get("limit", "50"))))
    except (ValueError, TypeError):
        limit = 50

    # Get open tickets from Fossil
    reader = FossilReader(repo.full_path)
    with reader:
        all_tickets = reader.get_tickets(status=status_filter, limit=500)

    # Get currently claimed ticket UUIDs
    from fossil.agent_claims import TicketClaim

    claimed_uuids = set(TicketClaim.objects.filter(repository=repo).values_list("ticket_uuid", flat=True))

    # Filter out claimed tickets
    unclaimed = []
    for t in all_tickets:
        if t.uuid not in claimed_uuids:
            unclaimed.append(
                {
                    "uuid": t.uuid,
                    "title": t.title,
                    "status": t.status,
                    "type": t.type,
                    "priority": t.priority,
                    "severity": t.severity,
                    "created": _isoformat(t.created),
                }
            )
        if len(unclaimed) >= limit:
            break

    return JsonResponse({"tickets": unclaimed, "total": len(unclaimed)})


# --- Server-Sent Events ---


@csrf_exempt
def api_events(request, slug):
    """Server-Sent Events stream for real-time repository events.

    GET /projects/<slug>/fossil/api/events

    Streams events as SSE:
    - checkin: new checkin pushed
    - ticket: ticket created/updated (by count change)
    - claim: ticket claimed/released/submitted
    - workspace: workspace created/merged/abandoned
    - review: code review created/updated

    Heartbeat sent every 15 seconds if no events. Poll interval: 5 seconds.
    """
    if request.method != "GET":
        return JsonResponse({"error": "GET required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    def event_stream():
        from fossil.agent_claims import TicketClaim
        from fossil.code_reviews import CodeReview
        from fossil.workspaces import AgentWorkspace

        # Snapshot current state to detect changes
        last_checkin_count = 0
        try:
            with FossilReader(repo.full_path) as reader:
                last_checkin_count = reader.get_checkin_count()
        except Exception:
            pass

        last_claim_id = TicketClaim.all_objects.filter(repository=repo).order_by("-pk").values_list("pk", flat=True).first() or 0
        last_workspace_id = AgentWorkspace.all_objects.filter(repository=repo).order_by("-pk").values_list("pk", flat=True).first() or 0
        last_review_id = CodeReview.all_objects.filter(repository=repo).order_by("-pk").values_list("pk", flat=True).first() or 0

        heartbeat_counter = 0

        while True:
            events = []

            # Check for new checkins
            try:
                with FossilReader(repo.full_path) as reader:
                    current_count = reader.get_checkin_count()
                    if current_count > last_checkin_count:
                        new_count = current_count - last_checkin_count
                        timeline = reader.get_timeline(limit=new_count, event_type="ci")
                        for entry in timeline:
                            events.append(
                                {
                                    "type": "checkin",
                                    "data": {
                                        "uuid": entry.uuid,
                                        "user": entry.user,
                                        "comment": entry.comment,
                                        "branch": entry.branch,
                                        "timestamp": _isoformat(entry.timestamp),
                                    },
                                }
                            )
                        last_checkin_count = current_count
            except Exception:
                pass

            # Check for new claims
            new_claims = TicketClaim.all_objects.filter(repository=repo, pk__gt=last_claim_id).order_by("pk")
            for claim in new_claims:
                events.append(
                    {
                        "type": "claim",
                        "data": {
                            "ticket_uuid": claim.ticket_uuid,
                            "agent_id": claim.agent_id,
                            "status": claim.status,
                            "claimed_at": _isoformat(claim.claimed_at),
                        },
                    }
                )
                last_claim_id = claim.pk

            # Check for new workspaces
            new_workspaces = AgentWorkspace.all_objects.filter(repository=repo, pk__gt=last_workspace_id).order_by("pk")
            for ws in new_workspaces:
                events.append(
                    {
                        "type": "workspace",
                        "data": {
                            "name": ws.name,
                            "branch": ws.branch,
                            "status": ws.status,
                            "agent_id": ws.agent_id,
                        },
                    }
                )
                last_workspace_id = ws.pk

            # Check for new code reviews
            new_reviews = CodeReview.all_objects.filter(repository=repo, pk__gt=last_review_id).order_by("pk")
            for review in new_reviews:
                events.append(
                    {
                        "type": "review",
                        "data": {
                            "id": review.pk,
                            "title": review.title,
                            "status": review.status,
                            "agent_id": review.agent_id,
                        },
                    }
                )
                last_review_id = review.pk

            # Yield events
            for event in events:
                yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"

            # Heartbeat every ~15 seconds (3 iterations * 5s sleep)
            heartbeat_counter += 1
            if not events and heartbeat_counter >= 3:
                yield ": heartbeat\n\n"
                heartbeat_counter = 0

            time.sleep(5)

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# --- Code Review API ---


@csrf_exempt
def api_review_create(request, slug):
    """Submit code changes for review.

    POST /projects/<slug>/fossil/api/reviews/create
    {
        "title": "Fix null pointer in auth module",
        "description": "The auth check was failing when ...",
        "diff": "--- a/src/auth.py\\n+++ b/src/auth.py\\n...",
        "files_changed": ["src/auth.py", "tests/test_auth.py"],
        "agent_id": "claude-abc",
        "workspace": "agent-fix-123",
        "ticket_uuid": "abc123..."
    }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    title = (data.get("title") or "").strip()
    if not title:
        return JsonResponse({"error": "Review title is required"}, status=400)

    diff = (data.get("diff") or "").strip()
    if not diff:
        return JsonResponse({"error": "Diff is required"}, status=400)

    # Resolve optional workspace reference
    workspace_name = (data.get("workspace") or "").strip()
    workspace_obj = None
    if workspace_name:
        from fossil.workspaces import AgentWorkspace

        workspace_obj = AgentWorkspace.objects.filter(repository=repo, name=workspace_name).first()

    # If linking to a ticket, verify the caller owns the claim
    ticket_uuid = (data.get("ticket_uuid") or "").strip()
    review_agent_id = (data.get("agent_id") or "").strip()
    if ticket_uuid:
        from fossil.agent_claims import TicketClaim

        claim = TicketClaim.objects.filter(repository=repo, ticket_uuid=ticket_uuid).first()
        if claim is not None and review_agent_id and claim.agent_id != review_agent_id:
            return JsonResponse({"error": "Cannot create a review for a ticket claimed by another agent"}, status=403)

    from fossil.code_reviews import CodeReview

    review = CodeReview.objects.create(
        repository=repo,
        workspace=workspace_obj,
        title=title,
        description=data.get("description", ""),
        diff=diff,
        files_changed=data.get("files_changed", []),
        agent_id=review_agent_id,
        ticket_uuid=ticket_uuid,
        created_by=user,
    )

    return JsonResponse(
        {
            "id": review.pk,
            "title": review.title,
            "description": review.description,
            "status": review.status,
            "agent_id": review.agent_id,
            "files_changed": review.files_changed,
            "created_at": _isoformat(review.created_at),
        },
        status=201,
    )


@csrf_exempt
def api_review_list(request, slug):
    """List code reviews for a repository, optionally filtered by status.

    GET /projects/<slug>/fossil/api/reviews
    Optional query params: status (pending, approved, changes_requested, merged)
    """
    if request.method != "GET":
        return JsonResponse({"error": "GET required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    from fossil.code_reviews import CodeReview

    qs = CodeReview.objects.filter(repository=repo)
    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)

    page, per_page = _paginate_params(request)
    total = qs.count()
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    reviews_page = qs[(page - 1) * per_page : page * per_page]

    reviews = []
    for r in reviews_page:
        reviews.append(
            {
                "id": r.pk,
                "title": r.title,
                "status": r.status,
                "agent_id": r.agent_id,
                "files_changed": r.files_changed,
                "comment_count": r.comments.count(),
                "created_at": _isoformat(r.created_at),
                "updated_at": _isoformat(r.updated_at),
            }
        )

    return JsonResponse(
        {
            "reviews": reviews,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }
    )


@csrf_exempt
def api_review_detail(request, slug, review_id):
    """Get a code review with its comments.

    GET /projects/<slug>/fossil/api/reviews/<id>
    """
    if request.method != "GET":
        return JsonResponse({"error": "GET required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo)
    if err is not None:
        return err

    from fossil.code_reviews import CodeReview

    review = CodeReview.objects.filter(repository=repo, pk=review_id).first()
    if review is None:
        return JsonResponse({"error": "Review not found"}, status=404)

    comments = []
    for c in review.comments.all():
        comments.append(
            {
                "id": c.pk,
                "body": c.body,
                "file_path": c.file_path,
                "line_number": c.line_number,
                "author": c.author,
                "created_at": _isoformat(c.created_at),
            }
        )

    return JsonResponse(
        {
            "id": review.pk,
            "title": review.title,
            "description": review.description,
            "diff": review.diff,
            "status": review.status,
            "agent_id": review.agent_id,
            "files_changed": review.files_changed,
            "ticket_uuid": review.ticket_uuid,
            "workspace": review.workspace.name if review.workspace else None,
            "comments": comments,
            "created_at": _isoformat(review.created_at),
            "updated_at": _isoformat(review.updated_at),
        }
    )


@csrf_exempt
def api_review_comment(request, slug, review_id):
    """Add a comment to a code review.

    POST /projects/<slug>/fossil/api/reviews/<id>/comment
    {
        "body": "This looks good but consider...",
        "file_path": "src/auth.py",
        "line_number": 42,
        "author": "human-reviewer"
    }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    from fossil.code_reviews import CodeReview, ReviewComment

    review = CodeReview.objects.filter(repository=repo, pk=review_id).first()
    if review is None:
        return JsonResponse({"error": "Review not found"}, status=404)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    body = (data.get("body") or "").strip()
    if not body:
        return JsonResponse({"error": "Comment body is required"}, status=400)

    # Determine author from auth context, not caller-supplied data
    if user:
        author = user.username
    elif token:
        author = f"token:{token.name}" if hasattr(token, "name") else "api-token"
    else:
        author = "anonymous"

    comment = ReviewComment.objects.create(
        review=review,
        body=body,
        file_path=data.get("file_path", ""),
        line_number=data.get("line_number"),
        author=author,
        created_by=user,
    )

    return JsonResponse(
        {
            "id": comment.pk,
            "body": comment.body,
            "file_path": comment.file_path,
            "line_number": comment.line_number,
            "author": comment.author,
            "created_at": _isoformat(comment.created_at),
        },
        status=201,
    )


@csrf_exempt
def api_review_approve(request, slug, review_id):
    """Approve a code review.

    POST /projects/<slug>/fossil/api/reviews/<id>/approve
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    from fossil.code_reviews import CodeReview

    review = CodeReview.objects.filter(repository=repo, pk=review_id).first()
    if review is None:
        return JsonResponse({"error": "Review not found"}, status=404)

    if review.status == "merged":
        return JsonResponse({"error": "Review is already merged"}, status=409)

    # Prevent self-approval: token-based callers (agents) must identify themselves
    # and cannot approve a review created by the same agent.
    # Session-auth users (human reviewers) are allowed since they represent human oversight.
    if token is not None and review.agent_id:
        try:
            data = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, ValueError):
            data = {}
        approver_agent_id = (data.get("agent_id") or "").strip()
        if not approver_agent_id:
            return JsonResponse(
                {"error": "agent_id is required for token-based review approval"},
                status=400,
            )
        if approver_agent_id == review.agent_id:
            return JsonResponse({"error": "Cannot approve your own review"}, status=403)

    review.status = "approved"
    review.save(update_fields=["status", "updated_at", "version"])

    return JsonResponse({"id": review.pk, "status": review.status})


@csrf_exempt
def api_review_request_changes(request, slug, review_id):
    """Request changes on a code review.

    POST /projects/<slug>/fossil/api/reviews/<id>/request-changes
    {"comment": "Please fix the error handling in auth.py"}
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    from fossil.code_reviews import CodeReview, ReviewComment

    review = CodeReview.objects.filter(repository=repo, pk=review_id).first()
    if review is None:
        return JsonResponse({"error": "Review not found"}, status=404)

    if review.status == "merged":
        return JsonResponse({"error": "Review is already merged"}, status=409)

    review.status = "changes_requested"
    review.save(update_fields=["status", "updated_at", "version"])

    # Optionally add a comment with the change request
    try:
        data = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    comment_body = (data.get("comment") or "").strip()
    if comment_body:
        author = user.username if user else "reviewer"
        ReviewComment.objects.create(
            review=review,
            body=comment_body,
            author=author,
            created_by=user,
        )

    return JsonResponse({"id": review.pk, "status": review.status})


@csrf_exempt
def api_review_merge(request, slug, review_id):
    """Merge an approved code review.

    POST /projects/<slug>/fossil/api/reviews/<id>/merge

    Only approved reviews can be merged. If the review is linked to a workspace,
    the workspace merge is triggered.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    project, repo = _get_repo(slug)
    user, token, err = _check_api_auth(request, project, repo, required_scope="write")
    if err is not None:
        return err

    if token is None and (user is None or not can_write_project(user, project)):
        return JsonResponse({"error": "Write access required"}, status=403)

    from fossil.code_reviews import CodeReview

    review = CodeReview.objects.filter(repository=repo, pk=review_id).first()
    if review is None:
        return JsonResponse({"error": "Review not found"}, status=404)

    if review.status == "merged":
        return JsonResponse({"error": "Review is already merged"}, status=409)

    if review.status != "approved":
        return JsonResponse({"error": "Review must be approved before merging"}, status=409)

    review.status = "merged"
    review.save(update_fields=["status", "updated_at", "version"])

    # If linked to a ticket claim, update the claim status
    if review.ticket_uuid:
        from fossil.agent_claims import TicketClaim

        claim = TicketClaim.objects.filter(repository=repo, ticket_uuid=review.ticket_uuid).first()
        if claim and claim.status in ("claimed", "submitted"):
            claim.status = "merged"
            claim.save(update_fields=["status", "updated_at", "version"])

    return JsonResponse({"id": review.pk, "status": review.status, "title": review.title})

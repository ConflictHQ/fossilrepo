"""JSON API endpoints for programmatic access to Fossil repositories.

All endpoints live under /projects/<slug>/fossil/api/.
Auth: Bearer token (APIToken or PersonalAccessToken) or session cookie.
All responses are JSON. All read endpoints check can_read_project.
"""

import math

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from fossil.api_auth import authenticate_request
from fossil.models import FossilRepository
from fossil.reader import FossilReader
from projects.access import can_read_project
from projects.models import Project


def _get_repo(slug):
    """Look up project and repository by slug, or return 404 JSON."""
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    repo = get_object_or_404(FossilRepository, project=project, deleted_at__isnull=True)
    return project, repo


def _check_api_auth(request, project, repo):
    """Authenticate request and check read access.

    Returns (user, token, error_response). If error_response is not None,
    the caller should return it immediately.
    """
    user, token, err = authenticate_request(request, repository=repo)
    if err is not None:
        return None, None, err

    # For project-scoped APITokens (no user), the token itself grants access
    # since it's already scoped to this repository.
    if token is not None and user is None:
        return user, token, None

    # For user-scoped auth (PAT or session), check project visibility
    if user is not None and not can_read_project(user, project):
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

import re

import markdown as md
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.safestring import mark_safe

from core.permissions import P
from projects.models import Project

from .models import FossilRepository
from .reader import FossilReader


def _render_fossil_content(content: str, project_slug: str = "") -> str:
    """Render content that may be Fossil wiki markup, HTML, or Markdown.

    Fossil wiki pages can contain:
    - Raw HTML (most Fossil wiki pages)
    - Fossil-specific markup: [link|text], <verbatim>...</verbatim>
    - Markdown (newer pages)
    """
    if not content:
        return ""

    # Detect format from the raw content BEFORE any transformations
    is_markdown = _is_markdown(content)

    if is_markdown:
        # Markdown: convert Fossil [/path|text] links to markdown links first
        content = re.sub(r"\[(/[^|\]]+)\|([^\]]+)\]", r"[\2](\1)", content)
        content = re.sub(r"<verbatim>(.*?)</verbatim>", r"```\n\1\n```", content, flags=re.DOTALL)
        html = md.markdown(content, extensions=["fenced_code", "tables", "toc"])
        return _rewrite_fossil_links(html, project_slug) if project_slug else html

    # Fossil wiki / HTML: convert Fossil-specific syntax to HTML
    content = re.sub(r"\[(/[^|\]]+)\|([^\]]+)\]", r'<a href="\1">\2</a>', content)
    content = re.sub(r"\[(https?://[^|\]]+)\|([^\]]+)\]", r'<a href="\1">\2</a>', content)
    content = re.sub(r"<verbatim>(.*?)</verbatim>", r"<pre><code>\1</code></pre>", content, flags=re.DOTALL)

    # Convert Fossil wiki list syntax: lines starting with "  *  " to <ul><li>
    lines = content.split("\n")
    result = []
    in_list = False
    for line in lines:
        stripped_line = line.strip()
        if re.match(r"^\*\s", stripped_line) or re.match(r"^\d+\.\s", stripped_line):
            if not in_list:
                result.append("<ul>")
                in_list = True
            item_text = re.sub(r"^[\*\d+\.]\s*", "", stripped_line)
            result.append(f"<li>{item_text}</li>")
        else:
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(line)
    if in_list:
        result.append("</ul>")

    content = "\n".join(result)

    # Wrap bare text blocks in <p> tags (lines not inside HTML tags)
    content = re.sub(r"\n\n(?!<)", "\n\n<p>", content)

    return _rewrite_fossil_links(content, project_slug) if project_slug else content


def _is_markdown(content: str) -> bool:
    """Detect if content is Markdown vs Fossil wiki/HTML.

    Heuristic: if the content starts with markdown-style headings (#),
    or has significant markdown syntax patterns, treat as markdown.
    """
    stripped = content.strip()
    # Starts with markdown heading
    if re.match(r"^#{1,6}\s", stripped):
        return True
    # Has multiple markdown headings
    if len(re.findall(r"^#{1,6}\s", stripped, re.MULTILINE)) >= 2:
        return True
    # Has markdown link references [text][ref]
    if re.search(r"\[.+\]\[.+\]", stripped):
        return True
    # Has markdown code fences
    if "```" in stripped:
        return True
    # Starts with HTML block element — it's Fossil wiki/HTML
    if re.match(r"<(h[1-6]|p|ol|ul|div|table)\b", stripped, re.IGNORECASE):
        return False
    return False


def _rewrite_fossil_links(html: str, project_slug: str) -> str:
    """Rewrite internal Fossil URLs to our app's URL structure.

    Fossil links like /doc/trunk/www/file.wiki, /info/HASH, /wiki/PageName,
    /tktview/HASH get mapped to our fossil app URLs.
    """
    if not project_slug:
        return html

    base = f"/projects/{project_slug}/fossil"

    def replace_link(match):
        url = match.group(1)
        # /info/HASH -> checkin detail
        m = re.match(r"/info/([0-9a-f]+)", url)
        if m:
            return f'href="{base}/checkin/{m.group(1)}/"'
        # /doc/trunk/www/file or /doc/tip/... -> code file view
        m = re.match(r"/doc/(?:trunk|tip|[^/]+)/(.+)", url)
        if m:
            return f'href="{base}/code/file/{m.group(1)}"'
        # /wiki?name=PageName -> wiki page (query string format)
        m = re.match(r"/wiki\?name=(.+)", url)
        if m:
            return f'href="{base}/wiki/page/{m.group(1)}"'
        # /wiki/PageName -> wiki page (path format)
        m = re.match(r"/wiki/(.+)", url)
        if m:
            return f'href="{base}/wiki/page/{m.group(1)}"'
        # /tktview/HASH or /tktview?name=HASH -> ticket detail
        m = re.match(r"/tktview[?/](?:name=)?([0-9a-f]+)", url)
        if m:
            return f'href="{base}/tickets/{m.group(1)}/"'
        # /timeline -> timeline
        if url.startswith("/timeline"):
            return f'href="{base}/timeline/"'
        # /forum -> forum
        if url.startswith("/forumpost") or url.startswith("/forum"):
            return f'href="{base}/forum/"'
        # Keep external and unrecognized links as-is
        return match.group(0)

    # Rewrite href="/..." links (internal Fossil paths)
    html = re.sub(r'href="(/[^"]*)"', replace_link, html)
    # Also rewrite href="/wiki?name=..." (markdown renders these with full path)
    html = re.sub(r'href="(/wiki\?[^"]*)"', replace_link, html)
    return html


def _get_repo_and_reader(slug):
    """Return (project, fossil_repo, reader) or raise 404."""
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    fossil_repo = get_object_or_404(FossilRepository, project=project, deleted_at__isnull=True)
    if not fossil_repo.exists_on_disk:
        raise Http404("Repository file not found on disk")
    reader = FossilReader(fossil_repo.full_path)
    return project, fossil_repo, reader


# --- Code Browser ---


@login_required
def code_browser(request, slug, dirpath=""):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    with reader:
        checkin_uuid = reader.get_latest_checkin_uuid()
        files = reader.get_files_at_checkin(checkin_uuid) if checkin_uuid else []
        metadata = reader.get_metadata()
        latest_commit = reader.get_timeline(limit=1, event_type="ci")

    # Build directory listing for the current path
    tree = _build_file_tree(files, current_dir=dirpath)

    # Build breadcrumbs
    breadcrumbs = []
    if dirpath:
        parts = dirpath.strip("/").split("/")
        for i, part in enumerate(parts):
            breadcrumbs.append({"name": part, "path": "/".join(parts[: i + 1])})

    if request.headers.get("HX-Request"):
        return render(request, "fossil/partials/file_tree.html", {"tree": tree, "project": project, "current_dir": dirpath})

    return render(
        request,
        "fossil/code_browser.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "tree": tree,
            "current_dir": dirpath,
            "breadcrumbs": breadcrumbs,
            "checkin_uuid": checkin_uuid,
            "metadata": metadata,
            "latest_commit": latest_commit[0] if latest_commit else None,
            "active_tab": "code",
        },
    )


@login_required
def code_file(request, slug, filepath):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    with reader:
        checkin_uuid = reader.get_latest_checkin_uuid()
        files = reader.get_files_at_checkin(checkin_uuid) if checkin_uuid else []

        # Find the file by path
        target = None
        for f in files:
            if f.name == filepath:
                target = f
                break

        if not target:
            raise Http404(f"File not found: {filepath}")

        content_bytes = reader.get_file_content(target.uuid)

    # Try to decode as text
    try:
        content = content_bytes.decode("utf-8")
        is_binary = False
    except UnicodeDecodeError:
        content = f"Binary file ({len(content_bytes)} bytes)"
        is_binary = True

    # Determine language for syntax highlighting
    ext = filepath.rsplit(".", 1)[-1] if "." in filepath else ""

    # Build breadcrumbs for file path
    parts = filepath.split("/")
    file_breadcrumbs = []
    for i, part in enumerate(parts):
        file_breadcrumbs.append({"name": part, "path": "/".join(parts[: i + 1])})

    # Split into lines for line-number display
    lines = content.split("\n") if not is_binary else []
    # Enumerate for template (1-indexed)
    numbered_lines = [{"num": i + 1, "text": line} for i, line in enumerate(lines)]

    return render(
        request,
        "fossil/code_file.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "filepath": filepath,
            "file_breadcrumbs": file_breadcrumbs,
            "content": content,
            "lines": numbered_lines,
            "line_count": len(lines),
            "is_binary": is_binary,
            "language": ext,
            "active_tab": "code",
        },
    )


# --- Checkin Detail ---


@login_required
def checkin_detail(request, slug, checkin_uuid):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    with reader:
        checkin = reader.get_checkin_detail(checkin_uuid)
        if not checkin:
            raise Http404("Checkin not found")

        # Compute diffs for each changed file
        import difflib

        file_diffs = []
        for f in checkin.files_changed:
            old_text = ""
            new_text = ""
            if f["prev_uuid"]:
                try:
                    old_bytes = reader.get_file_content(f["prev_uuid"])
                    old_text = old_bytes.decode("utf-8", errors="replace")
                except Exception:
                    old_text = ""
            if f["uuid"]:
                try:
                    new_bytes = reader.get_file_content(f["uuid"])
                    new_text = new_bytes.decode("utf-8", errors="replace")
                except Exception:
                    new_text = ""

            # Check if binary
            is_binary = "\x00" in old_text[:1024] or "\x00" in new_text[:1024]
            diff_lines = []
            additions = 0
            deletions = 0

            if not is_binary and (old_text or new_text):
                diff = difflib.unified_diff(
                    old_text.splitlines(keepends=True),
                    new_text.splitlines(keepends=True),
                    fromfile=f"a/{f['name']}",
                    tofile=f"b/{f['name']}",
                    lineterm="",
                    n=3,
                )
                old_line = 0
                new_line = 0
                for line in diff:
                    line_type = "context"
                    old_num = ""
                    new_num = ""
                    if line.startswith("+++") or line.startswith("---"):
                        line_type = "header"
                    elif line.startswith("@@"):
                        line_type = "hunk"
                        # Parse @@ -old_start,old_count +new_start,new_count @@
                        hunk_match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                        if hunk_match:
                            old_line = int(hunk_match.group(1))
                            new_line = int(hunk_match.group(2))
                    elif line.startswith("+"):
                        line_type = "add"
                        additions += 1
                        new_num = new_line
                        new_line += 1
                    elif line.startswith("-"):
                        line_type = "del"
                        deletions += 1
                        old_num = old_line
                        old_line += 1
                    else:
                        old_num = old_line
                        new_num = new_line
                        old_line += 1
                        new_line += 1
                    diff_lines.append({"text": line, "type": line_type, "old_num": old_num, "new_num": new_num})

            ext = f["name"].rsplit(".", 1)[-1] if "." in f["name"] else ""
            file_diffs.append(
                {
                    "name": f["name"],
                    "change_type": f["change_type"],
                    "uuid": f["uuid"],
                    "is_binary": is_binary,
                    "diff_lines": diff_lines,
                    "additions": additions,
                    "deletions": deletions,
                    "language": ext,
                }
            )

    return render(
        request,
        "fossil/checkin_detail.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "checkin": checkin,
            "file_diffs": file_diffs,
            "active_tab": "timeline",
        },
    )


# --- Timeline ---


@login_required
def timeline(request, slug):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    event_type = request.GET.get("type", "")
    page = int(request.GET.get("page", "1"))
    per_page = 50
    offset = (page - 1) * per_page

    with reader:
        entries = reader.get_timeline(limit=per_page, offset=offset, event_type=event_type or None)

    # Compute graph data for template
    graph_entries = _compute_dag_graph(entries)

    if request.headers.get("HX-Request"):
        return render(request, "fossil/partials/timeline_entries.html", {"entries": graph_entries, "project": project})

    return render(
        request,
        "fossil/timeline.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "entries": graph_entries,
            "event_type": event_type,
            "page": page,
            "active_tab": "timeline",
        },
    )


# --- Tickets ---


@login_required
def ticket_list(request, slug):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    status_filter = request.GET.get("status", "")
    search = request.GET.get("search", "").strip()

    with reader:
        tickets = reader.get_tickets(status=status_filter or None)

    if search:
        tickets = [t for t in tickets if search.lower() in t.title.lower()]

    if request.headers.get("HX-Request"):
        return render(request, "fossil/partials/ticket_table.html", {"tickets": tickets, "project": project})

    return render(
        request,
        "fossil/ticket_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "tickets": tickets,
            "status_filter": status_filter,
            "search": search,
            "active_tab": "tickets",
        },
    )


@login_required
def ticket_detail(request, slug, ticket_uuid):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    with reader:
        ticket = reader.get_ticket_detail(ticket_uuid)

    if not ticket:
        raise Http404("Ticket not found")

    return render(
        request,
        "fossil/ticket_detail.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "ticket": ticket,
            "active_tab": "tickets",
        },
    )


# --- Wiki ---


@login_required
def wiki_list(request, slug):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    with reader:
        pages = reader.get_wiki_pages()
        home_page = reader.get_wiki_page("Home")

    home_content_html = ""
    if home_page:
        home_content_html = mark_safe(_render_fossil_content(home_page.content, project_slug=slug))

    return render(
        request,
        "fossil/wiki_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "pages": pages,
            "home_page": home_page,
            "home_content_html": home_content_html,
            "active_tab": "wiki",
        },
    )


@login_required
def wiki_page(request, slug, page_name):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    with reader:
        page = reader.get_wiki_page(page_name)
        all_pages = reader.get_wiki_pages()

    if not page:
        raise Http404(f"Wiki page not found: {page_name}")

    content_html = mark_safe(_render_fossil_content(page.content, project_slug=slug))

    return render(
        request,
        "fossil/wiki_page.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "page": page,
            "all_pages": all_pages,
            "content_html": content_html,
            "active_tab": "wiki",
        },
    )


# --- Forum ---


@login_required
def forum_list(request, slug):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    with reader:
        posts = reader.get_forum_posts()

    return render(
        request,
        "fossil/forum_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "posts": posts,
            "active_tab": "forum",
        },
    )


@login_required
def forum_thread(request, slug, thread_uuid):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    with reader:
        posts = reader.get_forum_thread(thread_uuid)

    if not posts:
        raise Http404("Forum thread not found")

    return render(
        request,
        "fossil/forum_thread.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "posts": posts,
            "thread_uuid": thread_uuid,
            "active_tab": "forum",
        },
    )


# --- User Activity ---


@login_required
def user_activity(request, slug, username):
    P.PROJECT_VIEW.check(request.user)
    project, fossil_repo, reader = _get_repo_and_reader(slug)

    with reader:
        activity = reader.get_user_activity(username)

    return render(
        request,
        "fossil/user_activity.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "username": username,
            "activity": activity,
            "active_tab": "timeline",
        },
    )


# --- Helpers ---


def _build_file_tree(files, current_dir=""):
    """Build a flat sorted list for the directory view at a given path.

    Shows immediate children (dirs and files) of current_dir. Directories first.
    Each directory gets the most recent commit info from its descendants.
    """
    prefix = (current_dir.strip("/") + "/") if current_dir else ""
    prefix_len = len(prefix)

    dirs = {}  # immediate child dir name -> most recent file entry
    dir_files = []  # immediate child files

    for f in files:
        # Skip files with characters that break URL routing
        if "\n" in f.name or "\r" in f.name or "\x00" in f.name:
            continue
        # Only consider files under current_dir
        if not f.name.startswith(prefix):
            continue
        # Get the relative path after prefix
        relative = f.name[prefix_len:]
        parts = relative.split("/")

        if len(parts) > 1:
            # This file is inside a subdirectory
            child_dir = parts[0]
            if child_dir not in dirs or (
                f.last_commit_time and (not dirs[child_dir].last_commit_time or f.last_commit_time > dirs[child_dir].last_commit_time)
            ):
                dirs[child_dir] = f
        else:
            dir_files.append(f)

    entries = []
    # Directories first (sorted)
    for dir_name in sorted(dirs):
        f = dirs[dir_name]
        full_dir_path = (prefix + dir_name) if prefix else dir_name
        entries.append(
            {
                "name": dir_name,
                "path": full_dir_path,
                "is_dir": True,
                "commit_message": f.last_commit_message,
                "commit_time": f.last_commit_time,
            }
        )
    # Then files (sorted)
    for f in sorted(dir_files, key=lambda x: x.name):
        filename = f.name[prefix_len:] if prefix else f.name
        entries.append(
            {
                "name": filename,
                "path": f.name,
                "is_dir": False,
                "file": f,
                "commit_message": f.last_commit_message,
                "commit_time": f.last_commit_time,
            }
        )

    return entries


def _compute_dag_graph(entries):
    """Compute DAG graph positions for timeline entries.

    Returns a list of dicts wrapping each entry with graph rendering data:
    - node_x: pixel x position of the node
    - lines: list of (x1, x2) connections to draw between this row and the next
    """
    rail_pitch = 16  # pixels between rails
    rail_offset = 20  # left margin

    # Build rid-to-index lookup for connecting lines
    rid_to_idx = {}
    for i, entry in enumerate(entries):
        rid_to_idx[entry.rid] = i

    result = []
    for i, entry in enumerate(entries):
        rail = max(entry.rail, 0) if entry.rail >= 0 else 0
        node_x = rail_offset + rail * rail_pitch

        # Determine what vertical lines to draw through this row
        # Active rails: any branch that has entries above and below this point
        active_rails = set()

        # The current entry's rail is active if it has a parent below
        if entry.event_type == "ci" and entry.parent_rid in rid_to_idx:
            parent_idx = rid_to_idx[entry.parent_rid]
            if parent_idx > i:  # parent is below in the list (older)
                active_rails.add(rail)

        # Check if any entries above connect through this row to entries below
        for j in range(i):
            prev = entries[j]
            if prev.event_type == "ci" and prev.parent_rid in rid_to_idx:
                parent_idx = rid_to_idx[prev.parent_rid]
                if parent_idx > i:  # parent is below this row
                    prev_rail = max(prev.rail, 0)
                    active_rails.add(prev_rail)

        # Compute line segments as pixel positions
        lines = [{"x": rail_offset + r * rail_pitch} for r in sorted(active_rails)]

        # Connection from this node's rail to parent's rail (if different = branch/merge line)
        connector = None
        if entry.event_type == "ci" and entry.parent_rid in rid_to_idx:
            parent_idx = rid_to_idx[entry.parent_rid]
            if parent_idx == i + 1:  # immediate next entry
                parent_rail = max(entries[parent_idx].rail, 0)
                if parent_rail != rail:
                    parent_x = rail_offset + parent_rail * rail_pitch
                    connector = {
                        "left": min(node_x, parent_x),
                        "width": abs(node_x - parent_x),
                    }

        max_rail = max((e.rail for e in entries if e.rail >= 0), default=0)
        result.append(
            {
                "entry": entry,
                "node_x": node_x,
                "lines": lines,
                "connector": connector,
                "graph_width": rail_offset + (max_rail + 2) * rail_pitch,
            }
        )

    return result

import contextlib
import re

import markdown as md
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.safestring import mark_safe

from projects.models import Project

from .models import FossilRepository
from .reader import FossilReader


def _render_fossil_content(content: str, project_slug: str = "", base_path: str = "") -> str:
    """Render content that may be Fossil wiki markup, HTML, or Markdown.

    Fossil wiki pages can contain:
    - Raw HTML (most Fossil wiki pages)
    - Fossil-specific markup: [link|text], <verbatim>...</verbatim>
    - Markdown (newer pages)

    base_path: directory of the current file (e.g. "www/") for resolving relative links.
    """
    if not content:
        return ""

    # Detect format from the raw content BEFORE any transformations
    is_markdown = _is_markdown(content)

    if is_markdown:
        # Markdown: convert Fossil [path | text] links to markdown links first
        def _fossil_to_md_link(m):
            path = m.group(1).strip()
            text = m.group(2).strip()
            if path.startswith("./"):
                path = "/" + base_path + path[2:]
            elif not path.startswith("/") and not path.startswith("http"):
                path = "/" + base_path + path
            return f"[{text}]({path})"

        content = re.sub(r"\[([^\]\|]+?)\s*\|\s*([^\]]+?)\]", _fossil_to_md_link, content)
        content = re.sub(r"<verbatim>(.*?)</verbatim>", r"```\n\1\n```", content, flags=re.DOTALL)
        html = md.markdown(content, extensions=["fenced_code", "tables", "toc", "footnotes", "def_list", "attr_list"])

        # Post-process: render pikchr fenced code blocks to SVG
        def _render_pikchr_md(m):
            try:
                from fossil.cli import FossilCLI

                cli = FossilCLI()
                svg = cli.render_pikchr(m.group(1))
                if svg:
                    return f'<div class="pikchr-diagram">{svg}</div>'
            except Exception:
                pass
            return m.group(0)

        html = re.sub(r'<code class="language-pikchr">(.*?)</code>', _render_pikchr_md, html, flags=re.DOTALL)
        return _rewrite_fossil_links(html, project_slug) if project_slug else html

    # Fossil wiki / HTML: convert Fossil-specific syntax to HTML
    # Fossil links: [path | text] or [path|text] — spaces around pipe are optional
    def _fossil_link_replace(match):
        path = match.group(1).strip()
        text = match.group(2).strip()
        # Convert relative paths to absolute using base_path
        if path.startswith("./"):
            path = "/" + base_path + path[2:]
        elif not path.startswith("/") and not path.startswith("http"):
            path = "/" + base_path + path
        return f'<a href="{path}">{text}</a>'

    # Match [path | text] with flexible whitespace around the pipe
    content = re.sub(r"\[([^\]\|]+?)\s*\|\s*([^\]]+?)\]", _fossil_link_replace, content)
    # Interwiki links: [wikipedia:Article] -> external link
    content = re.sub(r"\[wikipedia:([^\]]+)\]", r'<a href="https://en.wikipedia.org/wiki/\1">\1</a>', content)
    # Anchor links: [#anchor-name] -> local anchor
    content = re.sub(r"\[#([^\]]+)\]", r'<a href="#\1">\1</a>', content)
    # Bare wiki links: [PageName] (no pipe, not a URL)
    content = re.sub(r"\[([A-Z][a-zA-Z0-9_]+)\]", r'<a href="\1">\1</a>', content)

    # Verbatim blocks
    # Pikchr diagrams: <verbatim type="pikchr">...</verbatim> → SVG
    def _render_pikchr_block(m):
        try:
            from fossil.cli import FossilCLI

            cli = FossilCLI()
            svg = cli.render_pikchr(m.group(1))
            if svg:
                return f'<div class="pikchr-diagram">{svg}</div>'
        except Exception:
            pass
        return f'<pre><code class="language-pikchr">{m.group(1)}</code></pre>'

    content = re.sub(r'<verbatim\s+type="pikchr">(.*?)</verbatim>', _render_pikchr_block, content, flags=re.DOTALL)
    # Regular verbatim blocks
    content = re.sub(r"<verbatim>(.*?)</verbatim>", r"<pre><code>\1</code></pre>", content, flags=re.DOTALL)
    # <nowiki> blocks — strip the tags, content passes through as-is
    content = re.sub(r"<nowiki>(.*?)</nowiki>", r"\1", content, flags=re.DOTALL)

    # Convert Fossil wiki list syntax: * bullets and # enumeration
    lines = content.split("\n")
    result = []
    in_list = False
    list_type = "ul"
    for line in lines:
        stripped_line = line.strip()
        is_bullet = re.match(r"^\*\s", stripped_line)
        is_enum = re.match(r"^#\s", stripped_line) or re.match(r"^\d+[\.\)]\s", stripped_line)
        if is_bullet or is_enum:
            new_type = "ol" if is_enum else "ul"
            if not in_list:
                list_type = new_type
                result.append(f"<{list_type}>")
                in_list = True
            elif new_type != list_type:
                result.append(f"</{list_type}>")
                list_type = new_type
                result.append(f"<{list_type}>")
            item_text = re.sub(r"^[\*#\d+\.\)]\s*", "", stripped_line)
            result.append(f"<li>{item_text}</li>")
        else:
            if in_list:
                result.append(f"</{list_type}>")
                in_list = False
            result.append(line)
    if in_list:
        result.append(f"</{list_type}>")

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
    # Starts with HTML block element — it's Fossil wiki/HTML; otherwise default to markdown
    return not re.match(r"<(h[1-6]|p|ol|ul|div|table)\b", stripped, re.IGNORECASE)


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
        # /vdiff?from=X&to=Y -> compare view
        m = re.match(r"/vdiff\?from=([0-9a-f]+)&to=([0-9a-f]+)", url)
        if m:
            return f'href="{base}/compare/?from={m.group(1)}&to={m.group(2)}"'
        # /timeline -> timeline
        if url.startswith("/timeline"):
            return f'href="{base}/timeline/"'
        # /forumpost/HASH -> forum thread
        m = re.match(r"/forumpost/([0-9a-f]+)", url)
        if m:
            return f'href="{base}/forum/{m.group(1)}/"'
        # /forum -> forum list
        if url.startswith("/forum"):
            return f'href="{base}/forum/"'
        # /www/file.wiki or /www/subdir/file -> doc page viewer
        m = re.match(r"/(www/.+)", url)
        if m:
            return f'href="{base}/docs/{m.group(1)}"'
        # /help/command -> Fossil help (link to fossil docs)
        m = re.match(r"/help/(.+)", url)
        if m:
            return f'href="{base}/docs/www/help.wiki"'
        # Bare .wiki or .md file paths (from relative link resolution)
        m = re.match(r"/([^/]+\.(?:wiki|md|html))", url)
        if m:
            return f'href="{base}/docs/www/{m.group(1)}"'
        # /dir -> our code browser
        if url == "/dir" or url.startswith("/dir?"):
            return f'href="{base}/code/"'
        # /builtin/path -> code file (these are embedded skin files)
        m = re.match(r"/builtin/(.+)", url)
        if m:
            return f'href="{base}/code/file/skins/{m.group(1)}"'
        # /setup_*, /admin_* -> Fossil server routes, no mapping
        if re.match(r"/(setup_|admin_)", url):
            return match.group(0)
        # Keep external and unrecognized links as-is
        return match.group(0)

    def replace_scheme_link(match):
        """Handle Fossil URI schemes like forum:/forumpost/HASH, wiki:PageName, info:HASH."""
        scheme = match.group(1)
        path = match.group(2)
        if scheme == "forum":
            # forum:/forumpost/HASH -> our forum thread
            m = re.match(r"/forumpost/([0-9a-f]+)", path)
            if m:
                return f'href="{base}/forum/{m.group(1)}/"'
        elif scheme == "info":
            return f'href="{base}/checkin/{path}/"'
        elif scheme == "wiki":
            return f'href="{base}/wiki/page/{path}"'
        return match.group(0)

    # Rewrite href="/..." links (internal Fossil paths)
    html = re.sub(r'href="(/[^"]*)"', replace_link, html)
    # Rewrite Fossil URI schemes: forum:/..., info:..., wiki:...
    html = re.sub(r'href="(forum|info|wiki):([^"]*)"', replace_scheme_link, html)

    # Rewrite external fossil-scm.org/home links (source repo) to local views
    # Do NOT rewrite fossil-scm.org/forum links — those are a separate repo/instance
    def replace_external_fossil(match):
        path = match.group(1)
        # /info/HASH
        m = re.match(r"/info/([0-9a-f]+)", path)
        if m:
            return f'href="{base}/checkin/{m.group(1)}/"'
        # /wiki/PageName
        m = re.match(r"/wiki/(.+)", path)
        if m:
            return f'href="{base}/wiki/page/{m.group(1)}"'
        # /doc/trunk/www/file -> docs
        m = re.match(r"/doc/(?:trunk|tip|[^/]+)/(.+)", path)
        if m:
            return f'href="{base}/docs/{m.group(1)}"'
        return match.group(0)

    html = re.sub(r'href="https?://(?:www\.)?fossil-scm\.org/home(/[^"]*)"', replace_external_fossil, html)

    # Also rewrite fossil-scm.org/forum links to our local forum
    def replace_external_forum(match):
        path = match.group(1)
        m = re.match(r"/forumpost/([0-9a-f]+)", path)
        if m:
            return f'href="{base}/forum/{m.group(1)}/"'
        return f'href="{base}/forum/"'

    html = re.sub(r'href="https?://(?:www\.)?fossil-scm\.org/forum(/[^"]*)"', replace_external_forum, html)
    return html


def _get_repo_and_reader(slug, request=None, require="read"):
    """Return (project, fossil_repo, reader) or raise 404/403.

    require: "read", "write", or "admin"
    """
    from projects.access import require_project_admin, require_project_read, require_project_write

    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)

    # Access check
    if request:
        if require == "admin":
            require_project_admin(request, project)
        elif require == "write":
            require_project_write(request, project)
        else:
            require_project_read(request, project)

    fossil_repo = get_object_or_404(FossilRepository, project=project, deleted_at__isnull=True)
    if not fossil_repo.exists_on_disk:
        raise Http404("Repository file not found on disk")
    reader = FossilReader(fossil_repo.full_path)
    return project, fossil_repo, reader


# --- Code Browser ---


def code_browser(request, slug, dirpath=""):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        checkin_uuid = reader.get_latest_checkin_uuid()
        files = reader.get_files_at_checkin(checkin_uuid) if checkin_uuid else []
        metadata = reader.get_metadata()
        latest_commit = reader.get_timeline(limit=1, event_type="ci")

    # Build directory listing for the current path
    tree = _build_file_tree(files, current_dir=dirpath)

    # Check for README in current directory
    readme_html = ""
    prefix = (dirpath.strip("/") + "/") if dirpath else ""
    for readme_name in ["README.md", "README", "README.txt", "README.wiki"]:
        full_name = prefix + readme_name
        for f in files:
            if f.name == full_name:
                with reader:
                    content_bytes = reader.get_file_content(f.uuid)
                try:
                    readme_content = content_bytes.decode("utf-8")
                    doc_base = prefix if prefix else ""
                    readme_html = mark_safe(_render_fossil_content(readme_content, project_slug=slug, base_path=doc_base))
                except (UnicodeDecodeError, Exception):
                    pass
                break
        if readme_html:
            break

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
            "readme_html": readme_html,
            "active_tab": "code",
        },
    )


def code_file(request, slug, filepath):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

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
    numbered_lines = [{"num": i + 1, "text": line} for i, line in enumerate(lines)]

    # Check if file can be rendered (wiki, markdown, html)
    can_render = ext in ("wiki", "md", "markdown", "html", "htm")
    view_mode = request.GET.get("mode", "source")
    rendered_html = ""
    if can_render and view_mode == "rendered" and not is_binary:
        doc_base = "/".join(filepath.split("/")[:-1])
        if doc_base:
            doc_base += "/"
        rendered_html = mark_safe(_render_fossil_content(content, project_slug=slug, base_path=doc_base))

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
            "can_render": can_render,
            "view_mode": view_mode,
            "rendered_html": rendered_html,
            "active_tab": "code",
        },
    )


# --- Checkin Detail ---


def checkin_detail(request, slug, checkin_uuid):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

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


def timeline(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

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


def ticket_list(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    status_filter = request.GET.get("status", "")
    search = request.GET.get("search", "").strip()
    page = int(request.GET.get("page", "1"))
    per_page = int(request.GET.get("per_page", "50"))
    per_page = per_page if per_page in (25, 50, 100) else 50

    with reader:
        tickets = reader.get_tickets(status=status_filter or None, limit=1000)

    if search:
        tickets = [t for t in tickets if search.lower() in t.title.lower()]

    total = len(tickets)
    import math

    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    tickets = tickets[(page - 1) * per_page : page * per_page]
    has_next = page < total_pages
    has_prev = page > 1

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
            "page": page,
            "per_page": per_page,
            "per_page_options": [25, 50, 100],
            "has_next": has_next,
            "has_prev": has_prev,
            "total": total,
            "total_pages": total_pages,
            "active_tab": "tickets",
        },
    )


def ticket_detail(request, slug, ticket_uuid):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        ticket = reader.get_ticket_detail(ticket_uuid)
        comments = reader.get_ticket_comments(ticket_uuid) if ticket else []

    if not ticket:
        raise Http404("Ticket not found")

    body_html = mark_safe(_render_fossil_content(ticket.body, project_slug=slug)) if ticket.body else ""
    rendered_comments = []
    for c in comments:
        rendered_comments.append(
            {
                "user": c["user"],
                "timestamp": c["timestamp"],
                "html": mark_safe(_render_fossil_content(c["comment"], project_slug=slug)),
            }
        )

    return render(
        request,
        "fossil/ticket_detail.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "ticket": ticket,
            "body_html": body_html,
            "comments": rendered_comments,
            "active_tab": "tickets",
        },
    )


# --- Wiki ---


def wiki_list(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

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


def wiki_page(request, slug, page_name):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

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


def forum_list(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

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


def forum_thread(request, slug, thread_uuid):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        posts = reader.get_forum_thread(thread_uuid)

    if not posts:
        raise Http404("Forum thread not found")

    # Render each post's body through the content renderer
    rendered_posts = []
    for post in posts:
        body_html = mark_safe(_render_fossil_content(post.body, project_slug=slug)) if post.body else ""
        rendered_posts.append({"post": post, "body_html": body_html})

    return render(
        request,
        "fossil/forum_thread.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "posts": rendered_posts,
            "thread_uuid": thread_uuid,
            "active_tab": "forum",
        },
    )


# --- Wiki CRUD ---


@login_required
def wiki_create(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    if request.method == "POST":
        page_name = request.POST.get("name", "").strip()
        content = request.POST.get("content", "")
        if page_name:
            from fossil.cli import FossilCLI

            cli = FossilCLI()
            # Try create first, fall back to commit (update)
            success = cli.wiki_create(fossil_repo.full_path, page_name, content)
            if not success:
                success = cli.wiki_commit(fossil_repo.full_path, page_name, content)
            if success:
                from django.contrib import messages

                messages.success(request, f'Wiki page "{page_name}" created.')
                from django.shortcuts import redirect

                return redirect("fossil:wiki_page", slug=slug, page_name=page_name)

    return render(request, "fossil/wiki_form.html", {"project": project, "active_tab": "wiki", "title": "New Wiki Page"})


@login_required
def wiki_edit(request, slug, page_name):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    with reader:
        page = reader.get_wiki_page(page_name)

    if not page:
        raise Http404(f"Wiki page not found: {page_name}")

    if request.method == "POST":
        content = request.POST.get("content", "")
        from fossil.cli import FossilCLI

        cli = FossilCLI()
        success = cli.wiki_commit(fossil_repo.full_path, page_name, content)
        if success:
            from django.contrib import messages

            messages.success(request, f'Wiki page "{page_name}" updated.')
            from django.shortcuts import redirect

            return redirect("fossil:wiki_page", slug=slug, page_name=page_name)

    return render(
        request,
        "fossil/wiki_form.html",
        {"project": project, "page": page, "active_tab": "wiki", "title": f"Edit: {page_name}"},
    )


# --- Ticket CRUD ---


@login_required
def ticket_create(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        body = request.POST.get("body", "")
        ticket_type = request.POST.get("type", "Code_Defect")
        severity = request.POST.get("severity", "")
        if title:
            from fossil.cli import FossilCLI

            cli = FossilCLI()
            fields = {"title": title, "type": ticket_type, "comment": body, "status": "Open"}
            if severity:
                fields["severity"] = severity
            success = cli.ticket_add(fossil_repo.full_path, fields)
            if success:
                from django.contrib import messages

                messages.success(request, f'Ticket "{title}" created.')
                from django.shortcuts import redirect

                return redirect("fossil:tickets", slug=slug)

    return render(request, "fossil/ticket_form.html", {"project": project, "active_tab": "tickets", "title": "New Ticket"})


@login_required
def ticket_edit(request, slug, ticket_uuid):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    with reader:
        ticket = reader.get_ticket_detail(ticket_uuid)
    if not ticket:
        raise Http404("Ticket not found")

    if request.method == "POST":
        from fossil.cli import FossilCLI

        cli = FossilCLI()
        fields = {}
        for field in ["title", "status", "type", "severity", "priority", "resolution", "subsystem"]:
            val = request.POST.get(field, "").strip()
            if val:
                fields[field] = val
        if fields:
            success = cli.ticket_change(fossil_repo.full_path, ticket.uuid, fields)
            if success:
                from django.contrib import messages

                messages.success(request, f'Ticket "{ticket.title}" updated.')
                from django.shortcuts import redirect

                return redirect("fossil:ticket_detail", slug=slug, ticket_uuid=ticket.uuid)

    return render(
        request,
        "fossil/ticket_edit.html",
        {"project": project, "ticket": ticket, "active_tab": "tickets"},
    )


@login_required
def ticket_comment(request, slug, ticket_uuid):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    if request.method == "POST":
        comment = request.POST.get("comment", "").strip()
        if comment:
            from fossil.cli import FossilCLI

            cli = FossilCLI()
            success = cli.ticket_change(fossil_repo.full_path, ticket_uuid, {"icomment": comment})
            if success:
                from django.contrib import messages

                messages.success(request, "Comment added.")
    from django.shortcuts import redirect

    return redirect("fossil:ticket_detail", slug=slug, ticket_uuid=ticket_uuid)


# --- User Activity ---


def user_activity(request, slug, username):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        activity = reader.get_user_activity(username)

    import json

    heatmap_json = json.dumps(activity.get("daily_activity", {}))

    return render(
        request,
        "fossil/user_activity.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "username": username,
            "activity": activity,
            "heatmap_json": heatmap_json,
            "active_tab": "timeline",
        },
    )


# --- Sync ---


@login_required
def sync_pull(request, slug):
    """Sync configuration and pull from upstream remote."""
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    from fossil.cli import FossilCLI

    cli = FossilCLI()
    result = None
    action = request.POST.get("action", "") if request.method == "POST" else ""

    # Auto-detect remote from .fossil file if not saved yet
    detected_remote = ""
    if not fossil_repo.remote_url and cli.is_available():
        detected_remote = cli.get_remote_url(fossil_repo.full_path)

    if action == "configure":
        # Save remote URL configuration
        url = request.POST.get("remote_url", "").strip()
        if url:
            fossil_repo.remote_url = url
            fossil_repo.save(update_fields=["remote_url", "updated_at", "version"])
            cli.ensure_default_user(fossil_repo.full_path)
            from django.contrib import messages

            messages.success(request, f"Sync configured: {url}")
            from django.shortcuts import redirect

            return redirect("fossil:sync", slug=slug)

    elif action == "disable":
        fossil_repo.remote_url = ""
        fossil_repo.last_sync_at = None
        fossil_repo.upstream_artifacts_available = 0
        fossil_repo.save(update_fields=["remote_url", "last_sync_at", "upstream_artifacts_available", "updated_at", "version"])
        from django.contrib import messages

        messages.info(request, "Sync disabled.")
        from django.shortcuts import redirect

        return redirect("fossil:sync", slug=slug)

    elif action == "pull" and fossil_repo.remote_url:
        if cli.is_available():
            cli.ensure_default_user(fossil_repo.full_path)
            result = cli.pull(fossil_repo.full_path)
            if result["success"]:
                from django.utils import timezone

                fossil_repo.last_sync_at = timezone.now()
                if result["artifacts_received"] > 0:
                    with reader:
                        fossil_repo.checkin_count = reader.get_checkin_count()
                        fossil_repo.file_size_bytes = fossil_repo.full_path.stat().st_size
                    fossil_repo.upstream_artifacts_available = 0
                fossil_repo.save(
                    update_fields=[
                        "last_sync_at",
                        "checkin_count",
                        "file_size_bytes",
                        "upstream_artifacts_available",
                        "updated_at",
                        "version",
                    ]
                )
                from django.contrib import messages

                if result["artifacts_received"] > 0:
                    messages.success(request, f"Pulled {result['artifacts_received']} new artifacts.")
                else:
                    messages.info(request, "Already up to date.")

    return render(
        request,
        "fossil/sync.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "detected_remote": detected_remote,
            "sync_configured": bool(fossil_repo.remote_url),
            "result": result,
            "active_tab": "sync",
        },
    )


# --- Technotes ---


def technote_list(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        notes = reader.get_technotes()

    return render(
        request,
        "fossil/technote_list.html",
        {"project": project, "notes": notes, "active_tab": "wiki"},
    )


# --- Compare Checkins ---


def compare_checkins(request, slug):
    """Compare two checkins side by side."""
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    from_uuid = request.GET.get("from", "")
    to_uuid = request.GET.get("to", "")

    from_detail = None
    to_detail = None
    file_diffs = []

    if from_uuid and to_uuid:
        with reader:
            from_detail = reader.get_checkin_detail(from_uuid)
            to_detail = reader.get_checkin_detail(to_uuid)

            if from_detail and to_detail:
                # Get all files from both checkins and compute diffs
                from_files = {f["name"]: f for f in from_detail.files_changed}
                to_files = {f["name"]: f for f in to_detail.files_changed}
                all_files = sorted(set(list(from_files.keys()) + list(to_files.keys())))

                import difflib

                for fname in all_files[:20]:  # Limit to 20 files for performance
                    old_text = ""
                    new_text = ""
                    f_from = from_files.get(fname, {})
                    f_to = to_files.get(fname, {})

                    if f_from.get("uuid"):
                        with contextlib.suppress(Exception):
                            old_text = reader.get_file_content(f_from["uuid"]).decode("utf-8", errors="replace")
                    if f_to.get("uuid"):
                        with contextlib.suppress(Exception):
                            new_text = reader.get_file_content(f_to["uuid"]).decode("utf-8", errors="replace")

                    if old_text != new_text:
                        diff = difflib.unified_diff(
                            old_text.splitlines(keepends=True),
                            new_text.splitlines(keepends=True),
                            fromfile=f"a/{fname}",
                            tofile=f"b/{fname}",
                            n=3,
                        )
                        diff_lines = []
                        for line in diff:
                            line_type = "context"
                            if line.startswith("+++") or line.startswith("---"):
                                line_type = "header"
                            elif line.startswith("@@"):
                                line_type = "hunk"
                            elif line.startswith("+"):
                                line_type = "add"
                            elif line.startswith("-"):
                                line_type = "del"
                            diff_lines.append({"text": line, "type": line_type})

                        if diff_lines:
                            file_diffs.append({"name": fname, "diff_lines": diff_lines})

    return render(
        request,
        "fossil/compare.html",
        {
            "project": project,
            "from_uuid": from_uuid,
            "to_uuid": to_uuid,
            "from_detail": from_detail,
            "to_detail": to_detail,
            "file_diffs": file_diffs,
            "active_tab": "timeline",
        },
    )


# --- Search ---


def search(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    query = request.GET.get("q", "").strip()
    results = None
    if query:
        with reader:
            results = reader.search(query, limit=20)

    return render(
        request,
        "fossil/search.html",
        {
            "project": project,
            "query": query,
            "results": results,
            "active_tab": "code",
        },
    )


# --- RSS Feed ---


def timeline_rss(request, slug):
    """RSS feed of recent timeline entries."""
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        entries = reader.get_timeline(limit=30, event_type="ci")

    from django.http import HttpResponse as DjHttpResponse
    from django.utils.html import escape

    items = []
    for e in entries:
        link = request.build_absolute_uri(f"/projects/{slug}/fossil/checkin/{e.uuid}/")
        items.append(
            f"<item><title>{escape(e.comment)}</title><link>{link}</link>"
            f"<author>{escape(e.user)}</author>"
            f"<pubDate>{e.timestamp.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
            f"<guid>{e.uuid}</guid></item>"
        )

    tl_link = request.build_absolute_uri(f"/projects/{slug}/fossil/timeline/")
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f"<title>{escape(project.name)} — Timeline</title>"
        f"<link>{tl_link}</link>"
        f"<description>Recent checkins for {escape(project.name)}</description>"
        f"{''.join(items)}"
        "</channel></rss>"
    )
    return DjHttpResponse(rss, content_type="application/rss+xml")


# --- CSV Export ---


def tickets_csv(request, slug):
    """Export all tickets as CSV."""
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        tickets = reader.get_tickets(limit=5000)

    import csv
    import io

    from django.http import HttpResponse as DjHttpResponse

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["UUID", "Title", "Status", "Type", "Priority", "Severity", "Created"])
    for t in tickets:
        writer.writerow([t.uuid, t.title, t.status, t.type, t.priority, t.severity, t.created.isoformat() if t.created else ""])

    response = DjHttpResponse(output.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{slug}-tickets.csv"'
    return response


# --- File History ---


def file_history(request, slug, filepath):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        history = reader.get_file_history(filepath)

    return render(
        request,
        "fossil/file_history.html",
        {
            "project": project,
            "filepath": filepath,
            "history": history,
            "active_tab": "code",
        },
    )


# --- Branches ---


def branch_list(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        branches = reader.get_branches()

    return render(
        request,
        "fossil/branch_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "branches": branches,
            "active_tab": "code",
        },
    )


# --- Tags ---


def tag_list(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        tags = reader.get_tags()

    return render(
        request,
        "fossil/tag_list.html",
        {"project": project, "tags": tags, "active_tab": "code"},
    )


# --- Raw File Download ---


@login_required
def code_raw(request, slug, filepath):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        checkin_uuid = reader.get_latest_checkin_uuid()
        files = reader.get_files_at_checkin(checkin_uuid) if checkin_uuid else []
        target = None
        for f in files:
            if f.name == filepath:
                target = f
                break
        if not target:
            raise Http404(f"File not found: {filepath}")
        content_bytes = reader.get_file_content(target.uuid)

    from django.http import HttpResponse as DjHttpResponse

    filename = filepath.split("/")[-1]
    response = DjHttpResponse(content_bytes, content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# --- File Blame ---


def code_blame(request, slug, filepath):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    from fossil.cli import FossilCLI

    cli = FossilCLI()
    blame_lines = []
    if cli.is_available():
        blame_lines = cli.blame(fossil_repo.full_path, filepath)

    parts = filepath.split("/")
    file_breadcrumbs = [{"name": p, "path": "/".join(parts[: i + 1])} for i, p in enumerate(parts)]

    return render(
        request,
        "fossil/code_blame.html",
        {
            "project": project,
            "filepath": filepath,
            "file_breadcrumbs": file_breadcrumbs,
            "blame_lines": blame_lines,
            "line_count": len(blame_lines),
            "active_tab": "code",
        },
    )


# --- Repository Statistics ---


def repo_stats(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        stats = reader.get_repo_statistics()
        top_contributors = reader.get_top_contributors(limit=15)
        activity = reader.get_commit_activity(weeks=52)

    import json

    return render(
        request,
        "fossil/repo_stats.html",
        {
            "project": project,
            "stats": stats,
            "top_contributors": top_contributors,
            "activity_json": json.dumps([c["count"] for c in activity]),
            "active_tab": "code",
        },
    )


# --- Fossil Docs ---

FOSSIL_SCM_SLUG = "fossil-scm"


def fossil_docs(request, slug):
    """Curated Fossil documentation index page."""
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    return render(request, "fossil/docs_index.html", {"project": project, "fossil_scm_slug": slug, "active_tab": "wiki"})


def fossil_doc_page(request, slug, doc_path):
    """Render a documentation file from the Fossil repo source tree."""
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        checkin_uuid = reader.get_latest_checkin_uuid()
        files = reader.get_files_at_checkin(checkin_uuid) if checkin_uuid else []

        target = None
        # Strip trailing slash for directory-style links
        clean_path = doc_path.rstrip("/")
        for f in files:
            if f.name == clean_path:
                target = f
                break

        # If not found, try index files for directory links
        if not target:
            for index_name in [f"{clean_path}/index.html", f"{clean_path}/index.md", f"{clean_path}/index.wiki"]:
                for f in files:
                    if f.name == index_name:
                        target = f
                        doc_path = index_name
                        break
                if target:
                    break

        if not target:
            raise Http404(f"Documentation file not found: {doc_path}")

        content_bytes = reader.get_file_content(target.uuid)

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise Http404("Binary file cannot be rendered as documentation") from e

    # Compute base_path for relative link resolution (e.g. "www/" for "www/concepts.wiki")
    doc_base = "/".join(doc_path.split("/")[:-1])
    if doc_base:
        doc_base += "/"
    content_html = mark_safe(_render_fossil_content(content, project_slug=slug, base_path=doc_base))

    return render(
        request,
        "fossil/doc_page.html",
        {"project": project, "doc_path": doc_path, "content_html": content_html, "active_tab": "wiki"},
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
                "size": f.size,
                "commit_message": f.last_commit_message,
                "commit_time": f.last_commit_time,
            }
        )

    return entries


def _compute_dag_graph(entries):
    """Compute DAG graph positions for timeline entries.

    Tracks active rails through each row and draws fork/merge connectors
    where a child is on a different rail than its parent.
    """
    rail_pitch = 16
    rail_offset = 20
    max_rail = max((e.rail for e in entries if e.rail >= 0), default=0)
    graph_width = rail_offset + (max_rail + 2) * rail_pitch

    # Build rid-to-index and rid-to-rail lookups
    rid_to_idx = {}
    rid_to_rail = {}
    for i, entry in enumerate(entries):
        rid_to_idx[entry.rid] = i
        if entry.event_type == "ci":
            rid_to_rail[entry.rid] = max(entry.rail, 0)

    # For each row, compute:
    # 1. Which vertical rails are active (have a line passing through)
    # 2. Whether there's a fork/merge connector to draw

    # Precompute: for each checkin, the range of rows its line spans
    # (from the entry's row to its parent's row)
    active_spans = []  # (rail, start_idx, end_idx)
    for i, entry in enumerate(entries):
        if entry.event_type == "ci" and entry.parent_rid in rid_to_idx:
            parent_idx = rid_to_idx[entry.parent_rid]
            if parent_idx > i:
                rail = max(entry.rail, 0)
                active_spans.append((rail, i, parent_idx))

    # Precompute connectors: for each row, collect all horizontal connections
    # A connector appears when a child on one rail connects to a parent on a different rail
    # We draw the connector at BOTH the child row (fork out) and on every row where
    # a branch line needs to cross from one rail to another
    row_connectors: dict[int, list[dict]] = {}
    for entry in entries:
        if entry.event_type != "ci" or entry.parent_rid not in rid_to_idx:
            continue
        parent_idx = rid_to_idx[entry.parent_rid]
        child_rail = max(entry.rail, 0)
        parent_rail = rid_to_rail.get(entry.parent_rid, 0)
        if child_rail != parent_rail:
            child_x = rail_offset + child_rail * rail_pitch
            parent_x = rail_offset + parent_rail * rail_pitch
            conn = {"left": min(child_x, parent_x), "width": abs(child_x - parent_x)}
            # Draw at the parent's row (where branch meets trunk)
            row_connectors.setdefault(parent_idx, []).append(conn)

    result = []
    for i, entry in enumerate(entries):
        rail = max(entry.rail, 0) if entry.rail >= 0 else 0
        node_x = rail_offset + rail * rail_pitch

        # Active rails at this row: any span that covers this row
        active_rails = set()
        for span_rail, span_start, span_end in active_spans:
            if span_start <= i <= span_end:
                active_rails.add(span_rail)

        lines = [{"x": rail_offset + r * rail_pitch} for r in sorted(active_rails)]
        connectors = row_connectors.get(i, [])

        result.append(
            {
                "entry": entry,
                "node_x": node_x,
                "lines": lines,
                "connectors": connectors,
                "graph_width": graph_width,
            }
        )

    return result

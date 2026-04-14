import contextlib
import math
import re
from datetime import datetime

import markdown as md
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_exempt

from core.pagination import PER_PAGE_OPTIONS, get_per_page, manual_paginate
from core.sanitize import sanitize_html
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
                path = "/" + base_path + path if base_path else "/wiki/" + path
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
            path = "/" + base_path + path if base_path else "/wiki/" + path
        return f'<a href="{path}">{text}</a>'

    # Match [path | text] with flexible whitespace around the pipe
    content = re.sub(r"\[([^\]\|]+?)\s*\|\s*([^\]]+?)\]", _fossil_link_replace, content)
    # Interwiki links: [wikipedia:Article] -> external link
    content = re.sub(r"\[wikipedia:([^\]]+)\]", r'<a href="https://en.wikipedia.org/wiki/\1">\1</a>', content)
    # Anchor links: [#anchor-name] -> local anchor
    content = re.sub(r"\[#([^\]]+)\]", r'<a href="#\1">\1</a>', content)
    # Bare wiki links: [PageName] (no pipe, not a URL) — use /wiki/ prefix so _rewrite_fossil_links maps it correctly
    content = re.sub(r"\[([A-Z][a-zA-Z0-9_]+)\]", r'<a href="/wiki/\1">\1</a>', content)

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

    # Do NOT rewrite fossil-scm.org/forum links — that's a separate Fossil
    # instance. If we have it locally as a different project, the user can
    # navigate there directly. Rewriting cross-repo links is fragile.
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
                    readme_html = mark_safe(sanitize_html(_render_fossil_content(readme_content, project_slug=slug, base_path=doc_base)))
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
        rendered_html = mark_safe(sanitize_html(_render_fossil_content(content, project_slug=slug, base_path=doc_base)))

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


# --- Diff helpers ---


def _parse_unified_diff_lines(raw_lines):
    """Parse raw unified diff output lines into structured diff_lines list.

    Works with both fossil diff and difflib output.
    Returns (diff_lines, additions, deletions) tuple.
    """
    diff_lines = []
    additions = 0
    deletions = 0
    old_line = 0
    new_line = 0

    for line in raw_lines:
        if line.startswith("====="):
            continue

        line_type = "context"
        old_num = ""
        new_num = ""

        if line.startswith("+++") or line.startswith("---"):
            line_type = "header"
        elif line.startswith("@@"):
            line_type = "hunk"
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

        if line_type in ("add", "del", "context") and len(line) > 0:
            prefix = line[0]
            code = line[1:]
        else:
            prefix = ""
            code = line

        diff_lines.append(
            {
                "text": line,
                "type": line_type,
                "old_num": old_num,
                "new_num": new_num,
                "prefix": prefix,
                "code": code,
            }
        )

    return diff_lines, additions, deletions


def _parse_fossil_diff_output(raw_output):
    """Split multi-file fossil diff output into per-file parsed diffs.

    Returns dict mapping filename -> (diff_lines, additions, deletions).
    """
    if not raw_output or not raw_output.strip():
        return {}

    result = {}
    current_name = None
    current_lines = []

    for line in raw_output.splitlines():
        if line.startswith("Index: "):
            if current_name is not None:
                result[current_name] = _parse_unified_diff_lines(current_lines)
            current_name = line[7:].strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        result[current_name] = _parse_unified_diff_lines(current_lines)

    return result


def _compute_split_lines(diff_lines):
    """Convert unified diff lines into parallel left/right arrays for split view.

    Context lines appear on both sides.  Deletions appear only on the left with
    an empty placeholder on the right.  Additions appear only on the right with
    an empty placeholder on the left.  Adjacent del+add runs are paired row-by-row
    so moves read naturally.
    """
    left = []
    right = []

    # Collect runs of consecutive del/add lines so we can pair them
    i = 0
    while i < len(diff_lines):
        dl = diff_lines[i]
        if dl["type"] in ("header", "hunk"):
            left.append(dl)
            right.append(dl)
            i += 1
            continue

        if dl["type"] == "del":
            # Gather contiguous del block, then contiguous add block
            dels = []
            while i < len(diff_lines) and diff_lines[i]["type"] == "del":
                dels.append(diff_lines[i])
                i += 1
            adds = []
            while i < len(diff_lines) and diff_lines[i]["type"] == "add":
                adds.append(diff_lines[i])
                i += 1
            max_len = max(len(dels), len(adds))
            for j in range(max_len):
                left.append(dels[j] if j < len(dels) else {"text": "", "type": "empty", "old_num": "", "new_num": ""})
                right.append(adds[j] if j < len(adds) else {"text": "", "type": "empty", "old_num": "", "new_num": ""})
            continue

        if dl["type"] == "add":
            # Orphan add with no preceding del
            left.append({"text": "", "type": "empty", "old_num": "", "new_num": ""})
            right.append(dl)
            i += 1
            continue

        # Context line
        left.append(dl)
        right.append(dl)
        i += 1

    return left, right


# --- Checkin Detail ---


def checkin_detail(request, slug, checkin_uuid):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        checkin = reader.get_checkin_detail(checkin_uuid)
        if not checkin:
            raise Http404("Checkin not found")

        # Try fossil native diff first for accurate results matching fossil-scm.org
        fossil_diffs = {}
        if checkin.parent_uuid:
            try:
                from .cli import FossilCLI

                cli = FossilCLI()
                raw_diff = cli.diff(fossil_repo.full_path, checkin.parent_uuid, checkin.uuid)
                if raw_diff:
                    fossil_diffs = _parse_fossil_diff_output(raw_diff)
            except Exception:
                pass

        file_diffs = []
        for f in checkin.files_changed:
            ext = f["name"].rsplit(".", 1)[-1] if "." in f["name"] else ""

            if f["name"] in fossil_diffs:
                diff_lines, additions, deletions = fossil_diffs[f["name"]]
                is_binary = False
            else:
                # Fallback: difflib for files fossil skipped (binary, no parent, etc.)
                import difflib

                old_text = ""
                new_text = ""
                if f["prev_uuid"]:
                    with contextlib.suppress(Exception):
                        old_text = reader.get_file_content(f["prev_uuid"]).decode("utf-8", errors="replace")
                if f["uuid"]:
                    with contextlib.suppress(Exception):
                        new_text = reader.get_file_content(f["uuid"]).decode("utf-8", errors="replace")

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
                    diff_lines, additions, deletions = _parse_unified_diff_lines(list(diff))

            split_left, split_right = _compute_split_lines(diff_lines)
            file_diffs.append(
                {
                    "name": f["name"],
                    "change_type": f["change_type"],
                    "uuid": f["uuid"],
                    "is_binary": is_binary,
                    "diff_lines": diff_lines,
                    "split_left": split_left,
                    "split_right": split_right,
                    "additions": additions,
                    "deletions": deletions,
                    "language": ext,
                }
            )

    # Fetch CI status checks for this checkin
    from fossil.ci import StatusCheck

    status_checks = StatusCheck.objects.filter(repository=fossil_repo, checkin_uuid=checkin_uuid)

    return render(
        request,
        "fossil/checkin_detail.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "checkin": checkin,
            "file_diffs": file_diffs,
            "status_checks": status_checks,
            "active_tab": "timeline",
        },
    )


# --- Timeline ---


def timeline(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    event_type = request.GET.get("type", "")
    page = int(request.GET.get("page", "1"))
    per_page = get_per_page(request, default=50)
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
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "timeline",
        },
    )


# --- Tickets ---


def ticket_list(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    status_filter = request.GET.get("status", "Open")
    if status_filter == "All":
        status_filter = ""
    search = request.GET.get("search", "").strip()
    page = int(request.GET.get("page", "1"))
    per_page = get_per_page(request, default=50)

    with reader:
        tickets = reader.get_tickets(status=status_filter or None, limit=1000)

    if search:
        tickets = [t for t in tickets if search.lower() in t.title.lower()]

    total = len(tickets)
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
            "per_page_options": PER_PAGE_OPTIONS,
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

    body_html = mark_safe(sanitize_html(_render_fossil_content(ticket.body, project_slug=slug))) if ticket.body else ""
    rendered_comments = []
    for c in comments:
        try:
            comment_html = mark_safe(sanitize_html(_render_fossil_content(c["comment"], project_slug=slug)))
        except Exception:
            comment_html = mark_safe(f"<pre>{c['comment']}</pre>")
        rendered_comments.append(
            {
                "user": c["user"],
                "timestamp": c["timestamp"],
                "html": comment_html,
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

    # Sort: Home first, then alphabetical
    pages = sorted(pages, key=lambda p: "" if p.name == "Home" else "~" + p.name.lower())

    search = request.GET.get("search", "").strip()
    if search:
        pages = [p for p in pages if search.lower() in p.name.lower()]

    per_page = get_per_page(request)
    pages, pagination = manual_paginate(pages, request, per_page=per_page)

    home_content_html = ""
    if home_page:
        home_content_html = mark_safe(sanitize_html(_render_fossil_content(home_page.content, project_slug=slug)))

    ctx = {
        "project": project,
        "fossil_repo": fossil_repo,
        "pages": pages,
        "home_page": home_page,
        "home_content_html": home_content_html,
        "search": search,
        "pagination": pagination,
        "per_page": per_page,
        "per_page_options": PER_PAGE_OPTIONS,
        "active_tab": "wiki",
    }

    if request.headers.get("HX-Request"):
        return render(request, "fossil/wiki_list.html", ctx)

    return render(request, "fossil/wiki_list.html", ctx)


def wiki_page(request, slug, page_name):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        page = reader.get_wiki_page(page_name)
        all_pages = reader.get_wiki_pages()

    if not page:
        raise Http404(f"Wiki page not found: {page_name}")

    # Sort: Home first, then alphabetical
    all_pages = sorted(all_pages, key=lambda p: "" if p.name == "Home" else "~" + p.name.lower())

    content_html = mark_safe(sanitize_html(_render_fossil_content(page.content, project_slug=slug)))

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
    from projects.access import can_write_project

    project, fossil_repo = _get_project_and_repo(slug, request, "read")

    # Read Fossil-native forum posts if the .fossil file exists
    fossil_posts = []
    if fossil_repo.exists_on_disk:
        with FossilReader(fossil_repo.full_path) as reader:
            fossil_posts = reader.get_forum_posts()

    # Merge Django-backed forum posts alongside Fossil native posts
    from fossil.forum import ForumPost as DjangoForumPost

    django_threads = DjangoForumPost.objects.filter(
        repository=fossil_repo,
        parent__isnull=True,
    ).select_related("created_by")

    # Build unified post list with a common interface
    merged = []
    for p in fossil_posts:
        merged.append({"uuid": p.uuid, "title": p.title, "body": p.body, "user": p.user, "timestamp": p.timestamp, "source": "fossil"})
    for p in django_threads:
        merged.append(
            {
                "uuid": str(p.pk),
                "title": p.title,
                "body": p.body,
                "user": p.created_by.username if p.created_by else "",
                "timestamp": p.created_at,
                "source": "django",
            }
        )

    # Sort merged list by timestamp descending
    merged.sort(key=lambda x: x["timestamp"], reverse=True)

    search = request.GET.get("search", "").strip()
    if search:
        search_lower = search.lower()
        merged = [p for p in merged if search_lower in (p.get("title") or "").lower() or search_lower in (p.get("body") or "").lower()]

    per_page = get_per_page(request)
    merged, pagination = manual_paginate(merged, request, per_page=per_page)

    has_write = can_write_project(request.user, project)

    return render(
        request,
        "fossil/forum_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "posts": merged,
            "has_write": has_write,
            "search": search,
            "pagination": pagination,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "forum",
        },
    )


def forum_thread(request, slug, thread_uuid):
    from projects.access import can_write_project

    project, fossil_repo = _get_project_and_repo(slug, request, "read")

    # Check if this is a Fossil-native thread or a Django-backed thread
    is_django_thread = False
    from fossil.forum import ForumPost as DjangoForumPost

    try:
        django_root = DjangoForumPost.objects.get(pk=int(thread_uuid), repository=fossil_repo)
        is_django_thread = True
    except (ValueError, DjangoForumPost.DoesNotExist):
        django_root = None

    rendered_posts = []

    if is_django_thread:
        # Django-backed thread: root + replies
        root = django_root
        body_html = mark_safe(sanitize_html(md.markdown(root.body, extensions=["fenced_code", "tables"]))) if root.body else ""
        rendered_posts.append(
            {
                "post": {
                    "user": root.created_by.username if root.created_by else "",
                    "title": root.title,
                    "timestamp": root.created_at,
                    "in_reply_to": "",
                },
                "body_html": body_html,
            }
        )
        for reply in DjangoForumPost.objects.filter(thread_root=root).exclude(pk=root.pk).select_related("created_by"):
            reply_html = mark_safe(sanitize_html(md.markdown(reply.body, extensions=["fenced_code", "tables"]))) if reply.body else ""
            rendered_posts.append(
                {
                    "post": {
                        "user": reply.created_by.username if reply.created_by else "",
                        "title": "",
                        "timestamp": reply.created_at,
                        "in_reply_to": str(root.pk),
                    },
                    "body_html": reply_html,
                }
            )
    else:
        # Fossil-native thread -- requires .fossil file on disk
        if not fossil_repo.exists_on_disk:
            raise Http404("Forum thread not found")

        with FossilReader(fossil_repo.full_path) as reader:
            posts = reader.get_forum_thread(thread_uuid)

        if not posts:
            raise Http404("Forum thread not found")

        for post in posts:
            body_html = mark_safe(sanitize_html(_render_fossil_content(post.body, project_slug=slug))) if post.body else ""
            rendered_posts.append({"post": post, "body_html": body_html})

    has_write = can_write_project(request.user, project)

    return render(
        request,
        "fossil/forum_thread.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "posts": rendered_posts,
            "thread_uuid": thread_uuid,
            "is_django_thread": is_django_thread,
            "has_write": has_write,
            "active_tab": "forum",
        },
    )


@login_required
def forum_create(request, slug):
    """Create a new Django-backed forum thread."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "write")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        body = request.POST.get("body", "")
        if title and body:
            from fossil.forum import ForumPost as DjangoForumPost

            post = DjangoForumPost.objects.create(
                repository=fossil_repo,
                title=title,
                body=body,
                created_by=request.user,
            )
            # Thread root is self for root posts
            post.thread_root = post
            post.save(update_fields=["thread_root", "updated_at", "version"])
            messages.success(request, f'Thread "{title}" created.')
            return redirect("fossil:forum_thread", slug=slug, thread_uuid=str(post.pk))

    return render(
        request,
        "fossil/forum_form.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "form_title": "New Thread",
            "active_tab": "forum",
        },
    )


@login_required
def forum_reply(request, slug, post_id):
    """Reply to a Django-backed forum thread."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "write")

    from fossil.forum import ForumPost as DjangoForumPost

    parent = get_object_or_404(DjangoForumPost, pk=post_id, repository=fossil_repo, deleted_at__isnull=True)

    # Determine the thread root
    thread_root = parent.thread_root if parent.thread_root else parent

    if request.method == "POST":
        body = request.POST.get("body", "")
        if body:
            DjangoForumPost.objects.create(
                repository=fossil_repo,
                title="",
                body=body,
                parent=parent,
                thread_root=thread_root,
                created_by=request.user,
            )
            messages.success(request, "Reply posted.")
            return redirect("fossil:forum_thread", slug=slug, thread_uuid=str(thread_root.pk))

    return render(
        request,
        "fossil/forum_form.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "parent": parent,
            "form_title": f"Reply to: {thread_root.title}",
            "active_tab": "forum",
        },
    )


# --- Webhook Management ---


@login_required
def webhook_list(request, slug):
    """List webhooks for a project."""
    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.webhooks import Webhook

    webhooks = Webhook.objects.filter(repository=fossil_repo)

    search = request.GET.get("search", "").strip()
    if search:
        webhooks = webhooks.filter(url__icontains=search)

    per_page = get_per_page(request)
    paginator = Paginator(webhooks, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(
        request,
        "fossil/webhook_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "webhooks": page_obj,
            "page_obj": page_obj,
            "search": search,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "settings",
        },
    )


@login_required
def webhook_create(request, slug):
    """Create a new webhook."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.webhooks import Webhook

    if request.method == "POST":
        url = request.POST.get("url", "").strip()
        secret = request.POST.get("secret", "").strip()
        events = request.POST.getlist("events")
        is_active = request.POST.get("is_active") == "on"

        if url:
            from core.url_validation import is_safe_outbound_url

            is_safe, url_error = is_safe_outbound_url(url)
            if not is_safe:
                messages.error(request, f"Invalid webhook URL: {url_error}")
            else:
                events_str = ",".join(events) if events else "all"
                Webhook.objects.create(
                    repository=fossil_repo,
                    url=url,
                    secret=secret,
                    events=events_str,
                    is_active=is_active,
                    created_by=request.user,
                )
                messages.success(request, "Webhook created.")
                return redirect("fossil:webhooks", slug=slug)

    return render(
        request,
        "fossil/webhook_form.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "form_title": "Create Webhook",
            "submit_label": "Create Webhook",
            "event_choices": Webhook.EventType.choices,
            "active_tab": "settings",
        },
    )


@login_required
def webhook_edit(request, slug, webhook_id):
    """Edit an existing webhook."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.webhooks import Webhook

    webhook = get_object_or_404(Webhook, pk=webhook_id, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        url = request.POST.get("url", "").strip()
        secret = request.POST.get("secret", "").strip()
        events = request.POST.getlist("events")
        is_active = request.POST.get("is_active") == "on"

        if url:
            from core.url_validation import is_safe_outbound_url

            is_safe, url_error = is_safe_outbound_url(url)
            if not is_safe:
                messages.error(request, f"Invalid webhook URL: {url_error}")
            else:
                webhook.url = url
                if secret:
                    webhook.secret = secret
                webhook.events = ",".join(events) if events else "all"
                webhook.is_active = is_active
                webhook.updated_by = request.user
                webhook.save()
                messages.success(request, "Webhook updated.")
                return redirect("fossil:webhooks", slug=slug)

    return render(
        request,
        "fossil/webhook_form.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "webhook": webhook,
            "form_title": f"Edit Webhook: {webhook.url}",
            "submit_label": "Update Webhook",
            "event_choices": Webhook.EventType.choices,
            "active_tab": "settings",
        },
    )


@login_required
def webhook_delete(request, slug, webhook_id):
    """Soft-delete a webhook."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.webhooks import Webhook

    webhook = get_object_or_404(Webhook, pk=webhook_id, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        webhook.soft_delete(user=request.user)
        messages.success(request, f"Webhook for {webhook.url} deleted.")
        return redirect("fossil:webhooks", slug=slug)

    return redirect("fossil:webhooks", slug=slug)


@login_required
def webhook_deliveries(request, slug, webhook_id):
    """View delivery log for a webhook."""
    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.webhooks import Webhook, WebhookDelivery

    webhook = get_object_or_404(Webhook, pk=webhook_id, repository=fossil_repo, deleted_at__isnull=True)
    deliveries = WebhookDelivery.objects.filter(webhook=webhook)[:100]

    return render(
        request,
        "fossil/webhook_deliveries.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "webhook": webhook,
            "deliveries": deliveries,
            "active_tab": "settings",
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

    from fossil.ticket_fields import TicketFieldDefinition

    try:
        custom_fields = list(TicketFieldDefinition.objects.filter(repository=fossil_repo))
    except Exception:
        custom_fields = []

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        body = request.POST.get("body", "")
        ticket_type = request.POST.get("type", "Code_Defect")
        severity = request.POST.get("severity", "")
        if title:
            from fossil.cli import FossilCLI

            cli = FossilCLI()
            priority = request.POST.get("priority", "")
            fields = {"title": title, "type": ticket_type, "comment": body, "status": "Open"}
            if severity:
                fields["severity"] = severity
            if priority:
                fields["priority"] = priority
            # Collect custom field values
            for cf in custom_fields:
                if cf.field_type == "checkbox":
                    val = "1" if request.POST.get(f"custom_{cf.name}") == "on" else "0"
                else:
                    val = request.POST.get(f"custom_{cf.name}", "").strip()
                if val:
                    fields[cf.name] = val
            success = cli.ticket_add(fossil_repo.full_path, fields)
            if success:
                from django.contrib import messages

                messages.success(request, f'Ticket "{title}" created.')
                from django.shortcuts import redirect

                return redirect("fossil:tickets", slug=slug)

    return render(
        request,
        "fossil/ticket_form.html",
        {"project": project, "active_tab": "tickets", "title": "New Ticket", "custom_fields": custom_fields},
    )


@login_required
def ticket_edit(request, slug, ticket_uuid):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    from fossil.ticket_fields import TicketFieldDefinition

    try:
        custom_fields = list(TicketFieldDefinition.objects.filter(repository=fossil_repo))
    except Exception:
        custom_fields = []

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
        # Collect custom field values
        for cf in custom_fields:
            if cf.field_type == "checkbox":
                val = "1" if request.POST.get(f"custom_{cf.name}") == "on" else "0"
            else:
                val = request.POST.get(f"custom_{cf.name}", "").strip()
            if val:
                fields[cf.name] = val
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
        {"project": project, "ticket": ticket, "custom_fields": custom_fields, "active_tab": "tickets"},
    )


@login_required
def ticket_comment(request, slug, ticket_uuid):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    if request.method == "POST":
        comment = request.POST.get("comment", "").strip()
        if comment:
            from django.contrib import messages

            try:
                from fossil.cli import FossilCLI

                cli = FossilCLI()
                success = cli.ticket_change(fossil_repo.full_path, ticket_uuid, {"icomment": comment})
                if success:
                    messages.success(request, "Comment added.")
                else:
                    messages.error(request, "Failed to add comment.")
            except Exception:
                messages.error(request, "Failed to add comment.")
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
            from core.url_validation import is_safe_outbound_url

            is_safe, url_error = is_safe_outbound_url(url)
            if not is_safe:
                from django.contrib import messages

                messages.error(request, f"Invalid remote URL: {url_error}")
                from django.shortcuts import redirect

                return redirect("fossil:sync", slug=slug)

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

    elif action in ("push", "sync_bidirectional") and fossil_repo.remote_url:
        from django.contrib import messages

        from projects.access import can_admin_project

        # Enforce branch protection — non-admins blocked if any protected branch restricts push
        push_blocked = False
        if not can_admin_project(request.user, project):
            from fossil.branch_protection import BranchProtection

            has_restrictions = BranchProtection.objects.filter(repository=fossil_repo, restrict_push=True, deleted_at__isnull=True).exists()
            if has_restrictions:
                push_blocked = True
                messages.error(
                    request,
                    "Push blocked: branch protection rules restrict push to admins only.",
                )

        if not push_blocked and cli.is_available():
            cli.ensure_default_user(fossil_repo.full_path)
            if action == "push":
                result = cli.push(fossil_repo.full_path)
                if result["success"]:
                    from django.utils import timezone

                    fossil_repo.last_sync_at = timezone.now()
                    fossil_repo.save(update_fields=["last_sync_at", "updated_at", "version"])
                    if result.get("artifacts_sent", 0) > 0:
                        messages.success(request, f"Pushed {result['artifacts_sent']} artifacts to remote.")
                    else:
                        messages.info(request, "Remote is already up to date.")
                else:
                    messages.error(request, f"Push failed: {result.get('message', 'Unknown error')}")
            else:
                result = cli.sync(fossil_repo.full_path)
                if result["success"]:
                    from django.utils import timezone

                    fossil_repo.last_sync_at = timezone.now()
                    with reader:
                        fossil_repo.checkin_count = reader.get_checkin_count()
                        fossil_repo.file_size_bytes = fossil_repo.full_path.stat().st_size
                    fossil_repo.save(update_fields=["last_sync_at", "checkin_count", "file_size_bytes", "updated_at", "version"])
                    messages.success(request, "Bidirectional sync complete.")
                else:
                    messages.error(request, f"Sync failed: {result.get('message', 'Unknown error')}")

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

    from fossil.sync_models import GitMirror

    mirrors = GitMirror.objects.filter(repository=fossil_repo, deleted_at__isnull=True)

    return render(
        request,
        "fossil/sync.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "detected_remote": detected_remote,
            "sync_configured": bool(fossil_repo.remote_url),
            "result": result,
            "mirrors": mirrors,
            "active_tab": "sync",
        },
    )


# --- Repository Settings ---


@login_required
def repo_settings(request, slug):
    """Repository settings: remote URL, storage info, danger zone."""
    from projects.access import require_project_admin

    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    require_project_admin(request, project)
    fossil_repo = get_object_or_404(FossilRepository, project=project, deleted_at__isnull=True)

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "update_remote":
            remote_url = request.POST.get("remote_url", "").strip()
            fossil_repo.remote_url = remote_url
            fossil_repo.save(update_fields=["remote_url", "updated_at", "version"])
            from django.contrib import messages

            messages.success(request, "Remote URL updated.")

        elif action == "sync_metadata":
            # Refresh metadata from the .fossil file
            if fossil_repo.exists_on_disk:
                with contextlib.suppress(Exception), FossilReader(fossil_repo.full_path) as reader:
                    meta = reader.get_metadata()
                    fossil_repo.checkin_count = meta.checkin_count
                    fossil_repo.fossil_project_code = meta.project_code
                    fossil_repo.file_size_bytes = fossil_repo.full_path.stat().st_size
                    fossil_repo.save(
                        update_fields=[
                            "checkin_count",
                            "fossil_project_code",
                            "file_size_bytes",
                            "updated_at",
                            "version",
                        ]
                    )
                from django.contrib import messages

                messages.success(request, "Metadata synced from repository file.")

        elif action == "pull_remote":
            if fossil_repo.remote_url and fossil_repo.exists_on_disk:
                from fossil.cli import FossilCLI

                cli = FossilCLI()
                if cli.is_available():
                    cli.ensure_default_user(fossil_repo.full_path)
                    result = cli.pull(fossil_repo.full_path)
                    from django.contrib import messages

                    if result["success"]:
                        from django.utils import timezone

                        fossil_repo.last_sync_at = timezone.now()
                        if result["artifacts_received"] > 0:
                            with FossilReader(fossil_repo.full_path) as rdr:
                                fossil_repo.checkin_count = rdr.get_checkin_count()
                            fossil_repo.file_size_bytes = fossil_repo.full_path.stat().st_size
                        fossil_repo.save(
                            update_fields=[
                                "last_sync_at",
                                "checkin_count",
                                "file_size_bytes",
                                "updated_at",
                                "version",
                            ]
                        )
                        if result["artifacts_received"] > 0:
                            messages.success(request, f"Pulled {result['artifacts_received']} new artifacts.")
                        else:
                            messages.info(request, "Already up to date.")
                    else:
                        messages.warning(request, f"Pull failed: {result['message']}")

        return redirect("fossil:repo_settings", slug=slug)

    # Gather repo info for display
    repo_info = {
        "exists_on_disk": fossil_repo.exists_on_disk,
    }
    if fossil_repo.exists_on_disk:
        repo_info["file_size"] = fossil_repo.full_path.stat().st_size
        repo_info["file_path"] = str(fossil_repo.full_path)
        with contextlib.suppress(Exception), FossilReader(fossil_repo.full_path) as reader:
            meta = reader.get_metadata()
            repo_info["project_name"] = meta.project_name
            repo_info["project_code"] = meta.project_code
            repo_info["checkin_count"] = meta.checkin_count
            repo_info["ticket_count"] = meta.ticket_count
            repo_info["wiki_page_count"] = meta.wiki_page_count

    return render(
        request,
        "fossil/repo_settings.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "repo_info": repo_info,
            "active_tab": "settings",
        },
    )


# --- Git Mirror ---


@login_required
def git_mirror_config(request, slug, mirror_id=None):
    """Configure Git mirror sync for a project.

    If mirror_id is provided, edit that mirror. Otherwise show the add form.
    """
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "admin")

    from fossil.sync_models import GitMirror

    mirrors = GitMirror.objects.filter(repository=fossil_repo, deleted_at__isnull=True)

    editing_mirror = None
    if mirror_id:
        editing_mirror = get_object_or_404(GitMirror, pk=mirror_id, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action in ("create", "update"):
            git_url = request.POST.get("git_remote_url", "").strip()
            auth_method = request.POST.get("auth_method", "token")
            auth_credential = request.POST.get("auth_credential", "").strip()
            # Use OAuth token from session if available and no manual credential provided
            if not auth_credential:
                if auth_method == "oauth_github" and request.session.get("github_oauth_token"):
                    auth_credential = request.session.pop("github_oauth_token")
                elif auth_method == "oauth_gitlab" and request.session.get("gitlab_oauth_token"):
                    auth_credential = request.session.pop("gitlab_oauth_token")
            sync_mode = request.POST.get("sync_mode", "scheduled")
            sync_direction = request.POST.get("sync_direction", "push")
            sync_schedule = request.POST.get("sync_schedule", "*/15 * * * *").strip()
            git_branch = request.POST.get("git_branch", "main").strip()
            fossil_branch = request.POST.get("fossil_branch", "trunk").strip()
            sync_tickets = request.POST.get("sync_tickets") == "on"
            sync_wiki = request.POST.get("sync_wiki") == "on"

            if git_url:
                from django.contrib import messages

                if action == "update" and editing_mirror:
                    editing_mirror.git_remote_url = git_url
                    editing_mirror.auth_method = auth_method
                    if auth_credential:  # Only update credential if a new one was provided
                        editing_mirror.auth_credential = auth_credential
                    editing_mirror.sync_mode = sync_mode
                    editing_mirror.sync_direction = sync_direction
                    editing_mirror.sync_schedule = sync_schedule
                    editing_mirror.git_branch = git_branch
                    editing_mirror.fossil_branch = fossil_branch
                    editing_mirror.sync_tickets = sync_tickets
                    editing_mirror.sync_wiki = sync_wiki
                    editing_mirror.updated_by = request.user
                    editing_mirror.save()
                    messages.success(request, f"Mirror updated: {git_url}")
                else:
                    GitMirror.objects.create(
                        repository=fossil_repo,
                        git_remote_url=git_url,
                        auth_method=auth_method,
                        auth_credential=auth_credential,
                        sync_mode=sync_mode,
                        sync_direction=sync_direction,
                        sync_schedule=sync_schedule,
                        git_branch=git_branch,
                        fossil_branch=fossil_branch,
                        sync_tickets=sync_tickets,
                        sync_wiki=sync_wiki,
                        created_by=request.user,
                    )
                    messages.success(request, f"Git mirror configured: {git_url}")

                return redirect("fossil:git_mirror", slug=slug)

    return render(
        request,
        "fossil/git_mirror.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "mirrors": mirrors,
            "editing_mirror": editing_mirror,
            "auth_method_choices": GitMirror.AuthMethod.choices,
            "sync_mode_choices": GitMirror.SyncMode.choices,
            "sync_direction_choices": GitMirror.SyncDirection.choices,
            "active_tab": "sync",
        },
    )


@login_required
def git_mirror_delete(request, slug, mirror_id):
    """Delete (soft-delete) a git mirror after confirmation."""
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "admin")

    from fossil.sync_models import GitMirror

    mirror = get_object_or_404(GitMirror, pk=mirror_id, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        from django.contrib import messages

        mirror.soft_delete(user=request.user)
        messages.success(request, f"Mirror to {mirror.git_remote_url} removed.")
        return redirect("fossil:sync", slug=slug)

    return render(
        request,
        "fossil/git_mirror_delete.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "mirror": mirror,
            "active_tab": "sync",
        },
    )


@login_required
def git_mirror_run(request, slug, mirror_id):
    """Manually trigger a Git sync for a specific mirror."""
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "admin")

    if request.method == "POST":
        from fossil.tasks import run_git_sync

        try:
            run_git_sync.delay(mirror_id)
            from django.contrib import messages

            messages.info(request, "Git sync triggered in background.")
        except Exception:
            # Celery not available — run synchronously
            run_git_sync(mirror_id)
            from django.contrib import messages

            messages.success(request, "Git sync completed.")

    from django.shortcuts import redirect

    return redirect("fossil:git_mirror", slug=slug)


# --- Fossil Wire Protocol Proxy (clone / push / pull) ---


@csrf_exempt
def fossil_xfer(request, slug):
    """Proxy Fossil sync protocol (clone/push/pull) through Django.

    GET  — informational page with clone URL.
    POST — pipe the request body through ``fossil http`` in CGI mode.

    Access control:
    - Public repos: anonymous clone/pull allowed (no --localauth).
    - Authenticated users with write access: full push/pull (--localauth).
    - Private/internal repos: require at least read permission.

    Supports HTTP Basic Auth for fossil CLI clients (push/pull/clone).
    """
    import base64

    from django.contrib.auth import authenticate

    from projects.access import can_read_project, can_write_project

    from .cli import FossilCLI

    # Fossil CLI sends HTTP Basic Auth — Django's session middleware ignores it,
    # so we authenticate manually from the Authorization header.
    if not request.user.is_authenticated:
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
                user = authenticate(request, username=username, password=password)
                if user and user.is_active:
                    request.user = user
            except (ValueError, UnicodeDecodeError):
                pass

    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    fossil_repo = get_object_or_404(FossilRepository, project=project, deleted_at__isnull=True)

    if request.method == "GET":
        if not can_read_project(request.user, project):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        import html as html_mod

        clone_url = request.build_absolute_uri()
        is_public = project.visibility == "public"
        auth_note = "" if is_public else "<p>Authentication is required.</p>"
        safe_name = html_mod.escape(project.name)
        safe_slug = html_mod.escape(project.slug)
        safe_url = html_mod.escape(clone_url)
        response_html = (
            f"<html><head><title>{safe_name} — Fossil Sync</title></head>"
            f"<body>"
            f"<h1>{safe_name}</h1>"
            f"<p>This is the Fossil sync endpoint for <strong>{safe_name}</strong>.</p>"
            f"<p>Clone with:</p>"
            f"<pre>fossil clone {safe_url} {safe_slug}.fossil</pre>"
            f"{auth_note}"
            f"</body></html>"
        )
        return HttpResponse(response_html)

    if request.method == "POST":
        if not fossil_repo.exists_on_disk:
            raise Http404("Repository file not found on disk.")

        from projects.access import can_admin_project

        has_write = can_write_project(request.user, project)
        has_read = can_read_project(request.user, project)

        if not has_read:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied

        # With --localauth, fossil grants full push access (for authenticated
        # writers).  Without it, fossil only allows pull/clone (for anonymous
        # or read-only users on public repos).
        localauth = has_write

        # Branch protection enforcement: if any protected branches restrict
        # push, only admins get --localauth (push access). Non-admins are
        # downgraded to read-only.
        if localauth and not can_admin_project(request.user, project):
            from fossil.branch_protection import BranchProtection

            has_restrictions = BranchProtection.objects.filter(repository=fossil_repo, restrict_push=True, deleted_at__isnull=True).exists()
            if has_restrictions:
                localauth = False

        # Required status checks enforcement: if any protected branches require
        # status checks, verify all required CI contexts have a passing latest
        # result before granting push access.
        if localauth and not can_admin_project(request.user, project):
            from fossil.branch_protection import BranchProtection
            from fossil.ci import StatusCheck

            protections_requiring_checks = BranchProtection.objects.filter(
                repository=fossil_repo, require_status_checks=True, deleted_at__isnull=True
            )
            for protection in protections_requiring_checks:
                required_contexts = protection.get_required_contexts_list()
                for context in required_contexts:
                    latest = StatusCheck.objects.filter(repository=fossil_repo, context=context).order_by("-created_at").first()
                    if not latest or latest.state != "success":
                        localauth = False
                        break
                if not localauth:
                    break

        cli = FossilCLI()
        body, content_type = cli.http_proxy(
            fossil_repo.full_path,
            request.body,
            request.content_type,
            localauth=localauth,
        )
        return HttpResponse(body, content_type=content_type)

    return HttpResponse(status=405)


# --- Watch / Notifications ---


@login_required
def toggle_watch(request, slug):
    """Toggle project watch on/off."""
    from fossil.notifications import ProjectWatch

    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        watch = ProjectWatch.objects.filter(user=request.user, project=project, deleted_at__isnull=True).first()
        if watch:
            watch.soft_delete(user=request.user)
            from django.contrib import messages

            messages.info(request, f"Unwatched {project.name}.")
        else:
            event_filter = request.POST.get("event_filter", "all")
            ProjectWatch.objects.create(user=request.user, project=project, event_filter=event_filter, created_by=request.user)
            from django.contrib import messages

            messages.success(request, f"Watching {project.name}. You'll get email notifications.")

    return redirect("projects:detail", slug=slug)


# --- OAuth ---


@login_required
def oauth_github_start(request, slug):
    """Start GitHub OAuth flow."""
    from fossil.oauth import github_authorize_url

    url = github_authorize_url(request, slug)
    if not url:
        from django.contrib import messages

        messages.error(request, "GitHub OAuth not configured. Set GITHUB_OAUTH_CLIENT_ID in admin settings.")
        return redirect("fossil:git_mirror", slug=slug)
    return redirect(url)


@login_required
def oauth_gitlab_start(request, slug):
    """Start GitLab OAuth flow."""
    from fossil.oauth import gitlab_authorize_url

    url = gitlab_authorize_url(request, slug)
    if not url:
        from django.contrib import messages

        messages.error(request, "GitLab OAuth not configured. Set GITLAB_OAUTH_CLIENT_ID in admin settings.")
        return redirect("fossil:git_mirror", slug=slug)
    return redirect(url)


@login_required
def oauth_github_callback(request, slug):
    """Handle GitHub OAuth callback."""
    from fossil.oauth import github_exchange_token

    result = github_exchange_token(request, slug)
    from django.contrib import messages

    if result["token"]:
        # Store token in session for the mirror config form to pick up
        request.session["github_oauth_token"] = result["token"]
        request.session["github_oauth_user"] = result.get("username", "")
        messages.success(request, f"Connected to GitHub as {result.get('username', 'unknown')}. Now configure your mirror.")
    else:
        messages.error(request, f"GitHub OAuth failed: {result.get('error', 'Unknown error')}")

    return redirect("fossil:git_mirror", slug=slug)


@login_required
def oauth_gitlab_callback(request, slug):
    """Handle GitLab OAuth callback."""
    from fossil.oauth import gitlab_exchange_token

    result = gitlab_exchange_token(request, slug)
    from django.contrib import messages

    if result["token"]:
        request.session["gitlab_oauth_token"] = result["token"]
        messages.success(request, "Connected to GitLab. Now configure your mirror.")
    else:
        messages.error(request, f"GitLab OAuth failed: {result.get('error', 'Unknown error')}")

    return redirect("fossil:git_mirror", slug=slug)


# --- Technotes ---


def technote_list(request, slug):
    from projects.access import can_write_project

    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        notes = reader.get_technotes()

    search = request.GET.get("search", "").strip()
    if search:
        search_lower = search.lower()
        notes = [n for n in notes if search_lower in (n.comment or "").lower()]

    per_page = get_per_page(request)
    notes, pagination = manual_paginate(notes, request, per_page=per_page)

    has_write = can_write_project(request.user, project)

    return render(
        request,
        "fossil/technote_list.html",
        {
            "project": project,
            "notes": notes,
            "has_write": has_write,
            "search": search,
            "pagination": pagination,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "wiki",
        },
    )


@login_required
def technote_create(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        body = request.POST.get("body", "")
        timestamp = request.POST.get("timestamp", "").strip()
        if title:
            from fossil.cli import FossilCLI

            cli = FossilCLI()
            ts = timestamp if timestamp else None
            success = cli.technote_create(
                fossil_repo.full_path,
                title,
                body,
                timestamp=ts,
                user=request.user.username,
            )
            if success:
                from django.contrib import messages

                messages.success(request, f'Technote "{title}" created.')
                return redirect("fossil:technotes", slug=slug)

    return render(
        request,
        "fossil/technote_form.html",
        {"project": project, "active_tab": "wiki", "form_title": "New Technote"},
    )


def technote_detail(request, slug, technote_id):
    from projects.access import can_write_project

    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        note = reader.get_technote_detail(technote_id)

    if not note:
        raise Http404("Technote not found")

    body_html = ""
    if note["body"]:
        body_html = mark_safe(sanitize_html(md.markdown(note["body"], extensions=["footnotes", "tables", "fenced_code"])))

    has_write = can_write_project(request.user, project)

    return render(
        request,
        "fossil/technote_detail.html",
        {
            "project": project,
            "note": note,
            "body_html": body_html,
            "has_write": has_write,
            "active_tab": "wiki",
        },
    )


@login_required
def technote_edit(request, slug, technote_id):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "write")

    with reader:
        note = reader.get_technote_detail(technote_id)

    if not note:
        raise Http404("Technote not found")

    if request.method == "POST":
        body = request.POST.get("body", "")
        from fossil.cli import FossilCLI

        cli = FossilCLI()
        success = cli.technote_edit(
            fossil_repo.full_path,
            technote_id,
            body,
            user=request.user.username,
        )
        if success:
            from django.contrib import messages

            messages.success(request, "Technote updated.")
            return redirect("fossil:technote_detail", slug=slug, technote_id=technote_id)

    return render(
        request,
        "fossil/technote_form.html",
        {
            "project": project,
            "note": note,
            "form_title": f"Edit Technote: {note['comment'][:60]}",
            "active_tab": "wiki",
        },
    )


# --- Unversioned Content ---


def unversioned_list(request, slug):
    from projects.access import can_admin_project

    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        files = reader.get_unversioned_files()

    search = request.GET.get("search", "").strip()
    if search:
        search_lower = search.lower()
        files = [f for f in files if search_lower in f.name.lower()]

    per_page = get_per_page(request)
    files, pagination = manual_paginate(files, request, per_page=per_page)

    has_admin = can_admin_project(request.user, project)

    return render(
        request,
        "fossil/unversioned_list.html",
        {
            "project": project,
            "files": files,
            "has_admin": has_admin,
            "search": search,
            "pagination": pagination,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "files",
        },
    )


def unversioned_download(request, slug, filename):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    import mimetypes

    from fossil.cli import FossilCLI

    cli = FossilCLI()
    try:
        content = cli.uv_cat(fossil_repo.full_path, filename)
    except FileNotFoundError as exc:
        raise Http404(f"Unversioned file not found: {filename}") from exc

    content_type, _ = mimetypes.guess_type(filename)
    if not content_type:
        content_type = "application/octet-stream"

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename.split("/")[-1]}"'
    response["Content-Length"] = len(content)
    return response


@login_required
def unversioned_upload(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, "admin")

    if request.method != "POST":
        return redirect("fossil:unversioned", slug=slug)

    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        from django.contrib import messages

        messages.error(request, "No file selected.")
        return redirect("fossil:unversioned", slug=slug)

    import tempfile

    from fossil.cli import FossilCLI

    cli = FossilCLI()

    # Write uploaded file to a temp location, then add via CLI
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        for chunk in uploaded_file.chunks():
            tmp.write(chunk)
        tmp_path = tmp.name

    from pathlib import Path

    try:
        success = cli.uv_add(fossil_repo.full_path, uploaded_file.name, Path(tmp_path))
        from django.contrib import messages

        if success:
            messages.success(request, f'File "{uploaded_file.name}" uploaded.')
        else:
            messages.error(request, f'Failed to upload "{uploaded_file.name}".')
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return redirect("fossil:unversioned", slug=slug)


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
                # Try fossil native diff first
                fossil_diffs = {}
                try:
                    from .cli import FossilCLI

                    cli = FossilCLI()
                    raw_diff = cli.diff(fossil_repo.full_path, from_uuid, to_uuid)
                    if raw_diff:
                        fossil_diffs = _parse_fossil_diff_output(raw_diff)
                except Exception:
                    pass

                if fossil_diffs:
                    for fname, (diff_lines, additions, deletions) in fossil_diffs.items():
                        if diff_lines:
                            split_left, split_right = _compute_split_lines(diff_lines)
                            file_diffs.append(
                                {
                                    "name": fname,
                                    "diff_lines": diff_lines,
                                    "split_left": split_left,
                                    "split_right": split_right,
                                    "additions": additions,
                                    "deletions": deletions,
                                }
                            )
                else:
                    # Fallback to difflib
                    import difflib

                    from_files = {f["name"]: f for f in from_detail.files_changed}
                    to_files = {f["name"]: f for f in to_detail.files_changed}
                    all_files = sorted(set(list(from_files.keys()) + list(to_files.keys())))

                    for fname in all_files[:20]:
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
                            diff_lines, additions, deletions = _parse_unified_diff_lines(list(diff))

                            if diff_lines:
                                split_left, split_right = _compute_split_lines(diff_lines)
                                file_diffs.append(
                                    {
                                        "name": fname,
                                        "diff_lines": diff_lines,
                                        "split_left": split_left,
                                        "split_right": split_right,
                                        "additions": additions,
                                        "deletions": deletions,
                                    }
                                )

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

    search = request.GET.get("search", "").strip()
    if search:
        search_lower = search.lower()
        branches = [b for b in branches if search_lower in b.name.lower()]

    per_page = get_per_page(request)
    branches, pagination = manual_paginate(branches, request, per_page=per_page)

    return render(
        request,
        "fossil/branch_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "branches": branches,
            "search": search,
            "pagination": pagination,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "branches",
        },
    )


# --- Tags ---


def tag_list(request, slug):
    project, fossil_repo, reader = _get_repo_and_reader(slug, request)

    with reader:
        tags = reader.get_tags()

    search = request.GET.get("search", "").strip()
    if search:
        search_lower = search.lower()
        tags = [t for t in tags if search_lower in t.name.lower()]

    per_page = get_per_page(request)
    tags, pagination = manual_paginate(tags, request, per_page=per_page)

    return render(
        request,
        "fossil/tag_list.html",
        {
            "project": project,
            "tags": tags,
            "search": search,
            "pagination": pagination,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "code",
        },
    )


# --- Raw File Download ---


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

    # Compute age-based coloring for blame annotations
    if blame_lines:
        dates = []
        for line in blame_lines:
            try:
                d = datetime.strptime(line["date"], "%Y-%m-%d")
                dates.append(d)
                line["_parsed_date"] = d
            except (ValueError, KeyError):
                line["_parsed_date"] = None

        if dates:
            min_date = min(dates)
            max_date = max(dates)
            date_range = (max_date - min_date).days or 1

            for line in blame_lines:
                age = (line["_parsed_date"] - min_date).days / date_range if line.get("_parsed_date") else 0.5
                # Interpolate from gray-500 (#6b7280) to brand (#DC394C)
                r = int(107 + age * (220 - 107))
                g = int(114 + age * (57 - 114))
                b = int(128 + age * (76 - 128))
                line["age_color"] = f"rgb({r},{g},{b})"
                line["age_bg"] = f"rgba({r},{g},{b},0.08)"
        else:
            for line in blame_lines:
                line["age_color"] = "rgb(107,114,128)"
                line["age_bg"] = "transparent"

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
    from projects.access import require_project_read

    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    require_project_read(request, project)
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
    content_html = mark_safe(sanitize_html(_render_fossil_content(content, project_slug=slug, base_path=doc_base)))

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


_RAIL_COLORS = [
    "#ef4444",  # 0: red
    "#3b82f6",  # 1: blue
    "#22c55e",  # 2: green
    "#f59e0b",  # 3: amber
    "#8b5cf6",  # 4: purple
    "#06b6d4",  # 5: cyan
    "#ec4899",  # 6: pink
    "#f97316",  # 7: orange
]


def _rail_color(rail: int) -> str:
    return _RAIL_COLORS[rail % len(_RAIL_COLORS)]


def _compute_dag_graph(entries):
    """Compute DAG graph positions for timeline entries.

    Tracks active rails through each row and draws fork/merge connectors
    where a child is on a different rail than its parent. Detects forks
    (first commit on a rail whose parent is on a different rail), merges
    (commits with multiple parents), and leaf tips (no child on the same rail).
    """
    if not entries:
        return []

    rail_pitch = 16
    rail_offset = 20
    max_rail = max((e.rail for e in entries if e.rail >= 0), default=0)
    graph_width = rail_offset + (max_rail + 2) * rail_pitch

    # Build rid-to-index and rid-to-rail lookups
    rid_to_idx: dict[int, int] = {}
    rid_to_rail: dict[int, int] = {}
    for i, entry in enumerate(entries):
        rid_to_idx[entry.rid] = i
        if entry.event_type == "ci":
            rid_to_rail[entry.rid] = max(entry.rail, 0)

    # Track which rids have a child on the same rail (for leaf detection).
    # Also track which rails have had a previous entry (for fork detection:
    # first entry on a rail whose parent is on a different rail = fork).
    has_child_on_rail: set[int] = set()  # parent rids that have a same-rail child
    rail_first_seen: dict[int, int] = {}  # rail -> index of first entry on that rail

    for i, entry in enumerate(entries):
        if entry.event_type != "ci":
            continue
        rail = max(entry.rail, 0)
        if rail not in rail_first_seen:
            rail_first_seen[rail] = i
        # Mark the primary parent as having a child on this rail
        if entry.parent_rid in rid_to_rail and rid_to_rail[entry.parent_rid] == rail:
            has_child_on_rail.add(entry.parent_rid)

    # Precompute: for each checkin, the range of rows its vertical line spans
    # (from the entry's row down to its parent's row, since entries are newest-first)
    active_spans: list[tuple[int, int, int]] = []  # (rail, start_idx, end_idx)
    for i, entry in enumerate(entries):
        if entry.event_type == "ci" and entry.parent_rid in rid_to_idx:
            parent_idx = rid_to_idx[entry.parent_rid]
            if parent_idx > i:
                rail = max(entry.rail, 0)
                active_spans.append((rail, i, parent_idx))

    # Precompute fork and merge connectors per row.
    # Fork: first entry on a rail whose primary parent is on a different rail.
    # Merge: entry with merge_parent_rids on different rails.
    row_connectors: dict[int, list[dict]] = {}
    row_fork_from: dict[int, int | None] = {}
    row_merge_to: dict[int, int | None] = {}

    for i, entry in enumerate(entries):
        if entry.event_type != "ci":
            continue
        child_rail = max(entry.rail, 0)

        # Fork detection: this entry's primary parent is on a different rail,
        # and this is the first entry we've seen on this rail.
        if entry.parent_rid in rid_to_rail:
            parent_rail = rid_to_rail[entry.parent_rid]
            if child_rail != parent_rail and rail_first_seen.get(child_rail) == i:
                row_fork_from[i] = parent_rail
                # Draw the fork connector at this row (where the branch starts)
                left_rail = min(child_rail, parent_rail)
                right_rail = max(child_rail, parent_rail)
                left_x = rail_offset + left_rail * rail_pitch
                right_x = rail_offset + right_rail * rail_pitch
                conn = {
                    "left": left_x,
                    "width": right_x - left_x,
                    "type": "fork",
                    "from_rail": parent_rail,
                    "to_rail": child_rail,
                    "color": _rail_color(child_rail),
                }
                row_connectors.setdefault(i, []).append(conn)

        # Merge detection: non-primary parents on different rails
        for merge_rid in entry.merge_parent_rids:
            if merge_rid in rid_to_rail:
                merge_rail = rid_to_rail[merge_rid]
                if merge_rail != child_rail:
                    row_merge_to[i] = child_rail
                    left_rail = min(child_rail, merge_rail)
                    right_rail = max(child_rail, merge_rail)
                    left_x = rail_offset + left_rail * rail_pitch
                    right_x = rail_offset + right_rail * rail_pitch
                    conn = {
                        "left": left_x,
                        "width": right_x - left_x,
                        "type": "merge",
                        "from_rail": merge_rail,
                        "to_rail": child_rail,
                        "color": _rail_color(merge_rail),
                    }
                    row_connectors.setdefault(i, []).append(conn)

    result = []
    for i, entry in enumerate(entries):
        rail = max(entry.rail, 0) if entry.rail >= 0 else 0
        node_x = rail_offset + rail * rail_pitch

        # Active rails at this row: any span that covers this row
        active_rails = set()
        for span_rail, span_start, span_end in active_spans:
            if span_start <= i <= span_end:
                active_rails.add(span_rail)

        lines = [{"x": rail_offset + r * rail_pitch, "color": _rail_color(r)} for r in sorted(active_rails)]
        connectors = row_connectors.get(i, [])

        # A leaf is a checkin that has no child on the same rail within this page
        is_leaf = entry.event_type == "ci" and entry.rid not in has_child_on_rail
        fork_from = row_fork_from.get(i)
        merge_to = row_merge_to.get(i)

        result.append(
            {
                "entry": entry,
                "node_x": node_x,
                "node_color": _rail_color(rail),
                "lines": lines,
                "connectors": connectors,
                "graph_width": graph_width,
                "fork_from": fork_from,
                "merge_to": merge_to,
                "is_merge": entry.is_merge,
                "is_leaf": is_leaf,
            }
        )

    return result


# --- Releases ---


def _get_project_and_repo(slug, request=None, require="read"):
    """Return (project, fossil_repo) without opening the .fossil file.

    Used by release views that only need Django ORM access, not Fossil SQLite queries.
    """
    from projects.access import require_project_admin, require_project_read, require_project_write

    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)

    if request:
        if require == "admin":
            require_project_admin(request, project)
        elif require == "write":
            require_project_write(request, project)
        else:
            require_project_read(request, project)

    fossil_repo = get_object_or_404(FossilRepository, project=project, deleted_at__isnull=True)
    return project, fossil_repo


def release_list(request, slug):
    from projects.access import can_write_project

    project, fossil_repo = _get_project_and_repo(slug, request, "read")

    from fossil.releases import Release

    releases = Release.objects.filter(repository=fossil_repo)

    has_write = can_write_project(request.user, project)
    if not has_write:
        releases = releases.filter(is_draft=False)

    search = request.GET.get("search", "").strip()
    if search:
        releases = releases.filter(tag_name__icontains=search) | releases.filter(name__icontains=search)
        releases = releases.distinct()

    per_page = get_per_page(request)
    paginator = Paginator(releases, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(
        request,
        "fossil/release_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "releases": page_obj,
            "page_obj": page_obj,
            "has_write": has_write,
            "search": search,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "releases",
        },
    )


def release_detail(request, slug, tag_name):
    from projects.access import can_admin_project, can_write_project

    project, fossil_repo = _get_project_and_repo(slug, request, "read")

    from fossil.releases import Release

    release = get_object_or_404(Release, repository=fossil_repo, tag_name=tag_name, deleted_at__isnull=True)

    # Drafts are only visible to writers
    if release.is_draft:
        from projects.access import require_project_write

        require_project_write(request, project)

    body_html = ""
    if release.body:
        body_html = mark_safe(sanitize_html(md.markdown(release.body, extensions=["footnotes", "tables", "fenced_code"])))

    assets = release.assets.filter(deleted_at__isnull=True)
    has_write = can_write_project(request.user, project)
    has_admin = can_admin_project(request.user, project)

    return render(
        request,
        "fossil/release_detail.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "release": release,
            "body_html": body_html,
            "assets": assets,
            "has_write": has_write,
            "has_admin": has_admin,
            "active_tab": "releases",
        },
    )


@login_required
def release_create(request, slug):
    from django.contrib import messages
    from django.utils import timezone

    project, fossil_repo = _get_project_and_repo(slug, request, "write")

    # Fetch recent checkins for the optional dropdown
    recent_checkins = []
    with contextlib.suppress(Exception):
        reader = FossilReader(fossil_repo.full_path)
        with reader:
            recent_checkins = reader.get_timeline(limit=20, event_type="ci")

    if request.method == "POST":
        from fossil.releases import Release

        tag_name = request.POST.get("tag_name", "").strip()
        name = request.POST.get("name", "").strip()
        body = request.POST.get("body", "")
        is_prerelease = request.POST.get("is_prerelease") == "on"
        is_draft = request.POST.get("is_draft") == "on"
        checkin_uuid = request.POST.get("checkin_uuid", "").strip()

        if tag_name and name:
            published_at = None if is_draft else timezone.now()
            release = Release.objects.create(
                repository=fossil_repo,
                tag_name=tag_name,
                name=name,
                body=body,
                is_prerelease=is_prerelease,
                is_draft=is_draft,
                published_at=published_at,
                checkin_uuid=checkin_uuid,
                created_by=request.user,
            )
            messages.success(request, f'Release "{release.tag_name}" created.')
            return redirect("fossil:release_detail", slug=slug, tag_name=release.tag_name)

    return render(
        request,
        "fossil/release_form.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "recent_checkins": recent_checkins,
            "form_title": "Create Release",
            "submit_label": "Create Release",
            "active_tab": "releases",
        },
    )


@login_required
def release_edit(request, slug, tag_name):
    from django.contrib import messages
    from django.utils import timezone

    project, fossil_repo = _get_project_and_repo(slug, request, "write")

    from fossil.releases import Release

    release = get_object_or_404(Release, repository=fossil_repo, tag_name=tag_name, deleted_at__isnull=True)

    # Fetch recent checkins for the optional dropdown
    recent_checkins = []
    with contextlib.suppress(Exception):
        reader = FossilReader(fossil_repo.full_path)
        with reader:
            recent_checkins = reader.get_timeline(limit=20, event_type="ci")

    if request.method == "POST":
        new_tag_name = request.POST.get("tag_name", "").strip()
        name = request.POST.get("name", "").strip()
        body = request.POST.get("body", "")
        is_prerelease = request.POST.get("is_prerelease") == "on"
        is_draft = request.POST.get("is_draft") == "on"
        checkin_uuid = request.POST.get("checkin_uuid", "").strip()

        if new_tag_name and name:
            was_draft = release.is_draft
            release.tag_name = new_tag_name
            release.name = name
            release.body = body
            release.is_prerelease = is_prerelease
            release.is_draft = is_draft
            release.checkin_uuid = checkin_uuid
            release.updated_by = request.user
            # Set published_at when transitioning from draft to published
            if was_draft and not is_draft and not release.published_at:
                release.published_at = timezone.now()
            release.save()
            messages.success(request, f'Release "{release.tag_name}" updated.')
            return redirect("fossil:release_detail", slug=slug, tag_name=release.tag_name)

    return render(
        request,
        "fossil/release_form.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "release": release,
            "recent_checkins": recent_checkins,
            "form_title": f"Edit Release: {release.tag_name}",
            "submit_label": "Update Release",
            "active_tab": "releases",
        },
    )


@login_required
def release_delete(request, slug, tag_name):
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.releases import Release

    release = get_object_or_404(Release, repository=fossil_repo, tag_name=tag_name, deleted_at__isnull=True)

    if request.method == "POST":
        release.soft_delete(user=request.user)
        messages.success(request, f'Release "{release.tag_name}" deleted.')
        return redirect("fossil:releases", slug=slug)

    return redirect("fossil:release_detail", slug=slug, tag_name=tag_name)


@login_required
def release_asset_upload(request, slug, tag_name):
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "write")

    from fossil.releases import Release, ReleaseAsset

    release = get_object_or_404(Release, repository=fossil_repo, tag_name=tag_name, deleted_at__isnull=True)

    if request.method == "POST" and request.FILES.get("file"):
        uploaded = request.FILES["file"]
        asset = ReleaseAsset.objects.create(
            release=release,
            name=uploaded.name,
            file=uploaded,
            file_size_bytes=uploaded.size,
            content_type=uploaded.content_type or "",
            created_by=request.user,
        )
        messages.success(request, f'Asset "{asset.name}" uploaded.')

    return redirect("fossil:release_detail", slug=slug, tag_name=tag_name)


def release_asset_download(request, slug, tag_name, asset_id):
    from django.db import models as db_models
    from django.http import FileResponse

    project, fossil_repo = _get_project_and_repo(slug, request, "read")

    from fossil.releases import Release, ReleaseAsset

    release = get_object_or_404(Release, repository=fossil_repo, tag_name=tag_name, deleted_at__isnull=True)

    if release.is_draft:
        from projects.access import require_project_write

        require_project_write(request, project)

    asset = get_object_or_404(ReleaseAsset, pk=asset_id, release=release, deleted_at__isnull=True)

    # Increment download count atomically
    ReleaseAsset.objects.filter(pk=asset.pk).update(download_count=db_models.F("download_count") + 1)

    return FileResponse(asset.file.open("rb"), as_attachment=True, filename=asset.name)


def release_source_archive(request, slug, tag_name, fmt):
    """Download source archive (tar.gz or zip) for a release's checkin."""
    from django.http import FileResponse

    project, fossil_repo = _get_project_and_repo(slug, request, "read")

    from fossil.releases import Release

    release = get_object_or_404(Release, repository=fossil_repo, tag_name=tag_name, deleted_at__isnull=True)

    if release.is_draft:
        from projects.access import require_project_write

        require_project_write(request, project)

    if not release.checkin_uuid:
        raise Http404("No checkin linked to this release.")

    from .cli import FossilCLI

    cli = FossilCLI()
    if fmt == "tar.gz":
        data = cli.tarball(fossil_repo.full_path, release.checkin_uuid)
        content_type = "application/gzip"
        filename = f"{project.slug}-{tag_name}.tar.gz"
    elif fmt == "zip":
        data = cli.zip_archive(fossil_repo.full_path, release.checkin_uuid)
        content_type = "application/zip"
        filename = f"{project.slug}-{tag_name}.zip"
    else:
        raise Http404

    if not data:
        raise Http404("Failed to generate archive.")

    import io

    return FileResponse(io.BytesIO(data), as_attachment=True, filename=filename, content_type=content_type)


# --- CI Status Check API ---


@csrf_exempt
def status_check_api(request, slug):
    """API endpoint for CI to report status checks.

    POST /projects/<slug>/fossil/api/status
    Authorization: Bearer <api_token>
    {
        "checkin": "abc123...",
        "context": "ci/tests",
        "state": "success",
        "description": "All 200 tests passed",
        "target_url": "https://ci.example.com/build/123"
    }

    GET /projects/<slug>/fossil/api/status?checkin=<uuid>
    Returns status checks for a specific checkin (public if project is public).
    """
    import json

    from fossil.api_tokens import authenticate_api_token
    from fossil.ci import StatusCheck

    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    fossil_repo = get_object_or_404(FossilRepository, project=project, deleted_at__isnull=True)

    if request.method == "GET":
        # Read access -- use normal project visibility rules
        from projects.access import can_read_project

        if not can_read_project(request.user, project):
            return JsonResponse({"error": "Access denied"}, status=403)

        checkin_uuid = request.GET.get("checkin", "")
        if not checkin_uuid:
            return JsonResponse({"error": "checkin parameter required"}, status=400)

        checks = StatusCheck.objects.filter(repository=fossil_repo, checkin_uuid=checkin_uuid)
        data = [
            {
                "context": c.context,
                "state": c.state,
                "description": c.description,
                "target_url": c.target_url,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in checks
        ]
        return JsonResponse({"checkin": checkin_uuid, "checks": data})

    if request.method == "POST":
        token = authenticate_api_token(request, fossil_repo)
        if not token:
            return JsonResponse({"error": "Invalid or expired token"}, status=401)

        if not token.has_permission("status:write"):
            return JsonResponse({"error": "Token lacks status:write permission"}, status=403)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        checkin_uuid = body.get("checkin", "").strip()
        context = body.get("context", "").strip()
        state = body.get("state", "").strip()
        description = body.get("description", "").strip()
        target_url = body.get("target_url", "").strip()

        if not checkin_uuid:
            return JsonResponse({"error": "checkin is required"}, status=400)
        if not context:
            return JsonResponse({"error": "context is required"}, status=400)
        if state not in StatusCheck.State.values:
            return JsonResponse({"error": f"state must be one of: {', '.join(StatusCheck.State.values)}"}, status=400)
        if len(context) > 200:
            return JsonResponse({"error": "context must be 200 characters or fewer"}, status=400)
        if len(description) > 500:
            return JsonResponse({"error": "description must be 500 characters or fewer"}, status=400)
        if target_url:
            from urllib.parse import urlparse

            parsed = urlparse(target_url)
            if parsed.scheme not in ("http", "https"):
                return JsonResponse({"error": "target_url must use http or https scheme"}, status=400)

        check, created = StatusCheck.objects.update_or_create(
            repository=fossil_repo,
            checkin_uuid=checkin_uuid,
            context=context,
            defaults={
                "state": state,
                "description": description,
                "target_url": target_url,
                "created_by": None,
            },
        )

        return JsonResponse(
            {
                "id": check.pk,
                "context": check.context,
                "state": check.state,
                "description": check.description,
                "target_url": check.target_url,
                "created": created,
            },
            status=201 if created else 200,
        )

    return JsonResponse({"error": "Method not allowed"}, status=405)


def status_badge(request, slug, checkin_uuid):
    """SVG badge for CI status (like shields.io).

    Returns an SVG image showing the aggregate status for all checks on a checkin.
    """
    from fossil.ci import StatusCheck

    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)

    # Badge endpoint is public for embeddability (like shields.io)
    fossil_repo = get_object_or_404(FossilRepository, project=project, deleted_at__isnull=True)

    checks = StatusCheck.objects.filter(repository=fossil_repo, checkin_uuid=checkin_uuid)

    if not checks.exists():
        label = "build"
        message = "unknown"
        color = "#9ca3af"  # gray
    else:
        states = set(checks.values_list("state", flat=True))
        if "error" in states or "failure" in states:
            label = "build"
            message = "failing"
            color = "#ef4444"  # red
        elif "pending" in states:
            label = "build"
            message = "pending"
            color = "#eab308"  # yellow
        else:
            label = "build"
            message = "passing"
            color = "#22c55e"  # green

    label_width = len(label) * 7 + 10
    message_width = len(message) * 7 + 10
    total_width = label_width + message_width

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20" role="img" aria-label="{label}: {message}">
  <title>{label}: {message}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{total_width}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="20" fill="#555"/>
    <rect x="{label_width}" width="{message_width}" height="20" fill="{color}"/>
    <rect width="{total_width}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="11">
    <text x="{label_width / 2}" y="14">{label}</text>
    <text x="{label_width + message_width / 2}" y="14">{message}</text>
  </g>
</svg>"""

    response = HttpResponse(svg, content_type="image/svg+xml")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# --- API Token Management ---


@login_required
def api_token_list(request, slug):
    """List API tokens for a project."""
    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.api_tokens import APIToken

    tokens = APIToken.objects.filter(repository=fossil_repo)

    search = request.GET.get("search", "").strip()
    if search:
        tokens = tokens.filter(name__icontains=search)

    per_page = get_per_page(request)
    paginator = Paginator(tokens, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(
        request,
        "fossil/api_token_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "tokens": page_obj,
            "page_obj": page_obj,
            "search": search,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "settings",
        },
    )


@login_required
def api_token_create(request, slug):
    """Generate a new API token. Shows the raw token once."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.api_tokens import APIToken

    raw_token = None

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        permissions = request.POST.get("permissions", "status:write").strip()
        expires_at = request.POST.get("expires_at", "").strip() or None

        if not name:
            messages.error(request, "Token name is required.")
        else:
            raw, token_hash, prefix = APIToken.generate()
            APIToken.objects.create(
                repository=fossil_repo,
                name=name,
                token_hash=token_hash,
                token_prefix=prefix,
                permissions=permissions,
                expires_at=expires_at,
                created_by=request.user,
            )
            raw_token = raw
            messages.success(request, f'Token "{name}" created. Copy it now -- it won\'t be shown again.')

    return render(
        request,
        "fossil/api_token_create.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "raw_token": raw_token,
            "active_tab": "settings",
        },
    )


@login_required
def api_token_delete(request, slug, token_id):
    """Revoke (soft-delete) an API token."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.api_tokens import APIToken

    token = get_object_or_404(APIToken, pk=token_id, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        token.soft_delete(user=request.user)
        messages.success(request, f'Token "{token.name}" revoked.')
        return redirect("fossil:api_tokens", slug=slug)

    return redirect("fossil:api_tokens", slug=slug)


# --- Branch Protection ---


@login_required
def branch_protection_list(request, slug):
    """List branch protection rules."""
    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.branch_protection import BranchProtection

    rules = BranchProtection.objects.filter(repository=fossil_repo)

    search = request.GET.get("search", "").strip()
    if search:
        rules = rules.filter(branch_pattern__icontains=search)

    per_page = get_per_page(request)
    paginator = Paginator(rules, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(
        request,
        "fossil/branch_protection_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "rules": page_obj,
            "page_obj": page_obj,
            "search": search,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "settings",
        },
    )


@login_required
def branch_protection_create(request, slug):
    """Create a new branch protection rule."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.branch_protection import BranchProtection

    if request.method == "POST":
        branch_pattern = request.POST.get("branch_pattern", "").strip()
        require_status_checks = request.POST.get("require_status_checks") == "on"
        required_contexts = request.POST.get("required_contexts", "").strip()
        restrict_push = request.POST.get("restrict_push") == "on"

        if not branch_pattern:
            messages.error(request, "Branch pattern is required.")
        elif BranchProtection.objects.filter(repository=fossil_repo, branch_pattern=branch_pattern).exists():
            messages.error(request, f'A rule for "{branch_pattern}" already exists.')
        else:
            BranchProtection.objects.create(
                repository=fossil_repo,
                branch_pattern=branch_pattern,
                require_status_checks=require_status_checks,
                required_contexts=required_contexts,
                restrict_push=restrict_push,
                created_by=request.user,
            )
            messages.success(request, f'Branch protection rule for "{branch_pattern}" created.')
            return redirect("fossil:branch_protections", slug=slug)

    return render(
        request,
        "fossil/branch_protection_form.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "form_title": "Create Branch Protection Rule",
            "submit_label": "Create Rule",
            "active_tab": "settings",
        },
    )


@login_required
def branch_protection_edit(request, slug, pk):
    """Edit a branch protection rule."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.branch_protection import BranchProtection

    rule = get_object_or_404(BranchProtection, pk=pk, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        branch_pattern = request.POST.get("branch_pattern", "").strip()
        require_status_checks = request.POST.get("require_status_checks") == "on"
        required_contexts = request.POST.get("required_contexts", "").strip()
        restrict_push = request.POST.get("restrict_push") == "on"

        if not branch_pattern:
            messages.error(request, "Branch pattern is required.")
        else:
            # Check uniqueness if pattern changed
            conflict = BranchProtection.objects.filter(repository=fossil_repo, branch_pattern=branch_pattern).exclude(pk=rule.pk).exists()
            if conflict:
                messages.error(request, f'A rule for "{branch_pattern}" already exists.')
            else:
                rule.branch_pattern = branch_pattern
                rule.require_status_checks = require_status_checks
                rule.required_contexts = required_contexts
                rule.restrict_push = restrict_push
                rule.updated_by = request.user
                rule.save()
                messages.success(request, f'Branch protection rule for "{rule.branch_pattern}" updated.')
                return redirect("fossil:branch_protections", slug=slug)

    return render(
        request,
        "fossil/branch_protection_form.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "rule": rule,
            "form_title": f"Edit Rule: {rule.branch_pattern}",
            "submit_label": "Update Rule",
            "active_tab": "settings",
        },
    )


@login_required
def branch_protection_delete(request, slug, pk):
    """Soft-delete a branch protection rule."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.branch_protection import BranchProtection

    rule = get_object_or_404(BranchProtection, pk=pk, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        rule.soft_delete(user=request.user)
        messages.success(request, f'Branch protection rule for "{rule.branch_pattern}" deleted.')
        return redirect("fossil:branch_protections", slug=slug)

    return redirect("fossil:branch_protections", slug=slug)


# ---------------------------------------------------------------------------
# Custom Ticket Fields
# ---------------------------------------------------------------------------


@login_required
def ticket_fields_list(request, slug):
    """List custom ticket field definitions for a project. Admin only."""
    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.ticket_fields import TicketFieldDefinition

    try:
        fields = TicketFieldDefinition.objects.filter(repository=fossil_repo)
        search = request.GET.get("search", "").strip()
        if search:
            fields = fields.filter(label__icontains=search) | fields.filter(name__icontains=search)
            fields = fields.distinct()
    except Exception:
        fields = TicketFieldDefinition.objects.none()

    per_page = get_per_page(request)
    paginator = Paginator(fields, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(
        request,
        "fossil/ticket_fields_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "fields": page_obj,
            "page_obj": page_obj,
            "search": search,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "settings",
        },
    )


@login_required
def ticket_fields_create(request, slug):
    """Create a new custom ticket field."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.ticket_fields import TicketFieldDefinition

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        label = request.POST.get("label", "").strip()
        field_type = request.POST.get("field_type", "text")
        choices_text = request.POST.get("choices", "").strip()
        is_required = request.POST.get("is_required") == "on"
        sort_order = int(request.POST.get("sort_order", "0") or "0")

        if name and label:
            if TicketFieldDefinition.objects.filter(repository=fossil_repo, name=name).exists():
                messages.error(request, f'A field named "{name}" already exists.')
            else:
                TicketFieldDefinition.objects.create(
                    repository=fossil_repo,
                    name=name,
                    label=label,
                    field_type=field_type,
                    choices=choices_text,
                    is_required=is_required,
                    sort_order=sort_order,
                    created_by=request.user,
                )
                messages.success(request, f'Custom field "{label}" created.')
                return redirect("fossil:ticket_fields", slug=slug)

    return render(
        request,
        "fossil/ticket_fields_form.html",
        {
            "project": project,
            "form_title": "Add Custom Ticket Field",
            "submit_label": "Create Field",
            "field_type_choices": TicketFieldDefinition.FieldType.choices,
            "active_tab": "settings",
        },
    )


@login_required
def ticket_fields_edit(request, slug, pk):
    """Edit an existing custom ticket field."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.ticket_fields import TicketFieldDefinition

    field_def = get_object_or_404(TicketFieldDefinition, pk=pk, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        label = request.POST.get("label", "").strip()
        field_type = request.POST.get("field_type", "text")
        choices_text = request.POST.get("choices", "").strip()
        is_required = request.POST.get("is_required") == "on"
        sort_order = int(request.POST.get("sort_order", "0") or "0")

        if name and label:
            dupe = TicketFieldDefinition.objects.filter(repository=fossil_repo, name=name).exclude(pk=field_def.pk).exists()
            if dupe:
                messages.error(request, f'A field named "{name}" already exists.')
            else:
                field_def.name = name
                field_def.label = label
                field_def.field_type = field_type
                field_def.choices = choices_text
                field_def.is_required = is_required
                field_def.sort_order = sort_order
                field_def.updated_by = request.user
                field_def.save()
                messages.success(request, f'Custom field "{label}" updated.')
                return redirect("fossil:ticket_fields", slug=slug)

    return render(
        request,
        "fossil/ticket_fields_form.html",
        {
            "project": project,
            "field_def": field_def,
            "form_title": f"Edit Field: {field_def.label}",
            "submit_label": "Save Changes",
            "field_type_choices": TicketFieldDefinition.FieldType.choices,
            "active_tab": "settings",
        },
    )


@login_required
def ticket_fields_delete(request, slug, pk):
    """Soft-delete a custom ticket field."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.ticket_fields import TicketFieldDefinition

    field_def = get_object_or_404(TicketFieldDefinition, pk=pk, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        field_def.soft_delete(user=request.user)
        messages.success(request, f'Custom field "{field_def.label}" deleted.')
        return redirect("fossil:ticket_fields", slug=slug)

    return redirect("fossil:ticket_fields", slug=slug)


# ---------------------------------------------------------------------------
# Custom Ticket Reports
# ---------------------------------------------------------------------------


def ticket_reports_list(request, slug):
    """List available ticket reports for a project."""
    from projects.access import can_admin_project

    project, fossil_repo = _get_project_and_repo(slug, request, "read")

    from fossil.ticket_reports import TicketReport

    reports = TicketReport.objects.filter(repository=fossil_repo)
    is_admin = can_admin_project(request.user, project)
    if not is_admin:
        reports = reports.filter(is_public=True)

    search = request.GET.get("search", "").strip()
    if search:
        reports = reports.filter(title__icontains=search) | reports.filter(description__icontains=search)
        reports = reports.distinct()

    per_page = get_per_page(request)
    paginator = Paginator(reports, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(
        request,
        "fossil/ticket_reports_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "reports": page_obj,
            "page_obj": page_obj,
            "can_admin": is_admin,
            "search": search,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
            "active_tab": "tickets",
        },
    )


@login_required
def ticket_report_create(request, slug):
    """Create a new ticket report. Admin only."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.ticket_reports import TicketReport

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        sql_query = request.POST.get("sql_query", "").strip()
        is_public = request.POST.get("is_public") == "on"

        if title and sql_query:
            error = TicketReport.validate_sql(sql_query)
            if error:
                messages.error(request, f"Invalid SQL: {error}")
            else:
                TicketReport.objects.create(
                    repository=fossil_repo,
                    title=title,
                    description=description,
                    sql_query=sql_query,
                    is_public=is_public,
                    created_by=request.user,
                )
                messages.success(request, f'Report "{title}" created.')
                return redirect("fossil:ticket_reports", slug=slug)

    return render(
        request,
        "fossil/ticket_report_form.html",
        {
            "project": project,
            "form_title": "Create Ticket Report",
            "submit_label": "Create Report",
            "active_tab": "tickets",
        },
    )


@login_required
def ticket_report_edit(request, slug, pk):
    """Edit an existing ticket report. Admin only."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    from fossil.ticket_reports import TicketReport

    report = get_object_or_404(TicketReport, pk=pk, repository=fossil_repo, deleted_at__isnull=True)

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        sql_query = request.POST.get("sql_query", "").strip()
        is_public = request.POST.get("is_public") == "on"

        if title and sql_query:
            error = TicketReport.validate_sql(sql_query)
            if error:
                messages.error(request, f"Invalid SQL: {error}")
            else:
                report.title = title
                report.description = description
                report.sql_query = sql_query
                report.is_public = is_public
                report.updated_by = request.user
                report.save()
                messages.success(request, f'Report "{title}" updated.')
                return redirect("fossil:ticket_reports", slug=slug)

    return render(
        request,
        "fossil/ticket_report_form.html",
        {
            "project": project,
            "report": report,
            "form_title": f"Edit Report: {report.title}",
            "submit_label": "Save Changes",
            "active_tab": "tickets",
        },
    )


def ticket_report_run(request, slug, pk):
    """Execute a ticket report and display results."""
    import sqlite3

    from projects.access import can_admin_project

    project, fossil_repo = _get_project_and_repo(slug, request, "read")

    from fossil.ticket_reports import TicketReport

    report = get_object_or_404(TicketReport, pk=pk, repository=fossil_repo, deleted_at__isnull=True)

    # Non-public reports require admin access
    if not report.is_public and not can_admin_project(request.user, project):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("This report is not public.")

    # Re-validate the SQL at execution time (defense in depth)
    error = TicketReport.validate_sql(report.sql_query)
    columns = []
    rows = []

    if error:
        pass  # error is shown in template
    else:
        # Replace placeholders with named parameters for safe execution
        sql = report.sql_query
        status_param = request.GET.get("status", "")
        type_param = request.GET.get("type", "")
        sql = sql.replace("{status}", ":status").replace("{type}", ":type")
        params = {"status": status_param, "type": type_param}

        # Execute against the Fossil SQLite file in read-only mode
        repo_path = fossil_repo.full_path
        uri = f"file:{repo_path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
            try:
                cursor = conn.execute(sql, params)
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = [list(row) for row in cursor.fetchall()[:1000]]
            except sqlite3.OperationalError as e:
                error = f"SQL error: {e}"
            finally:
                conn.close()
        except sqlite3.Error as e:
            error = f"Database error: {e}"

    return render(
        request,
        "fossil/ticket_report_results.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "report": report,
            "columns": columns,
            "rows": rows,
            "error": error,
            "active_tab": "tickets",
        },
    )


# --- Artifact Shunning ---


@login_required
def shun_list_view(request, slug):
    """List shunned artifacts and provide form to shun new ones. Admin only."""
    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    shunned = []
    if fossil_repo.exists_on_disk:
        from fossil.cli import FossilCLI

        cli = FossilCLI()
        if cli.is_available():
            shunned = cli.shun_list(fossil_repo.full_path)

    return render(
        request,
        "fossil/shun_list.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "shunned": shunned,
            "active_tab": "settings",
        },
    )


@login_required
def shun_artifact(request, slug):
    """Shun (permanently remove) an artifact. POST only. Admin only."""
    from django.contrib import messages

    project, fossil_repo = _get_project_and_repo(slug, request, "admin")

    if request.method != "POST":
        return redirect("fossil:shun_list", slug=slug)

    artifact_uuid = request.POST.get("artifact_uuid", "").strip()
    confirmation = request.POST.get("confirmation", "").strip()
    reason = request.POST.get("reason", "").strip()

    if not artifact_uuid:
        messages.error(request, "Artifact UUID is required.")
        return redirect("fossil:shun_list", slug=slug)

    # Validate UUID format: should be hex characters, 4-64 chars (Fossil uses SHA1/SHA3 hashes)
    if not re.match(r"^[0-9a-fA-F]{4,64}$", artifact_uuid):
        messages.error(request, "Invalid artifact UUID format. Must be a hex hash (4-64 characters).")
        return redirect("fossil:shun_list", slug=slug)

    # Require the user to type the first 8 chars of the UUID to confirm
    expected_confirmation = artifact_uuid[:8].lower()
    if confirmation.lower() != expected_confirmation:
        messages.error(request, f'Confirmation failed. You must type "{expected_confirmation}" to confirm shunning.')
        return redirect("fossil:shun_list", slug=slug)

    if not fossil_repo.exists_on_disk:
        messages.error(request, "Repository file not found on disk.")
        return redirect("fossil:shun_list", slug=slug)

    from fossil.cli import FossilCLI

    cli = FossilCLI()
    if not cli.is_available():
        messages.error(request, "Fossil binary is not available.")
        return redirect("fossil:shun_list", slug=slug)

    result = cli.shun(fossil_repo.full_path, artifact_uuid, reason=reason)
    if result["success"]:
        messages.success(request, f"Artifact {artifact_uuid[:12]}... has been permanently shunned.")
    else:
        messages.error(request, f"Failed to shun artifact: {result['message']}")

    return redirect("fossil:shun_list", slug=slug)


# --- SQLite Explorer ---

# Known relationships between Fossil tables (SQLite FKs are not enforced in .fossil files).
FOSSIL_RELATIONSHIPS = [
    ("event", "blob", "objid -> rid"),
    ("mlink", "blob", "mid -> rid, fid -> rid"),
    ("plink", "blob", "cid -> rid, pid -> rid"),
    ("tagxref", "tag", "tagid -> tagid"),
    ("tagxref", "blob", "srcid -> rid, origid -> rid"),
    ("delta", "blob", "rid -> rid, srcid -> rid"),
    ("leaf", "blob", "rid -> rid"),
    ("phantom", "blob", "rid -> rid"),
    ("ticketchng", "blob", "tkt_rid -> rid"),
    ("forumpost", "blob", "fpid -> rid"),
]

# Category colors for the schema visualization.
FOSSIL_TABLE_CATEGORIES = {
    # VCS core
    "blob": "blue",
    "delta": "blue",
    "event": "blue",
    "mlink": "blue",
    "plink": "blue",
    "leaf": "blue",
    "phantom": "blue",
    "rcvfrom": "blue",
    "filename": "blue",
    "repo_cksum": "blue",
    "config": "blue",
    # Tagging / branching
    "tag": "indigo",
    "tagxref": "indigo",
    # Tickets
    "ticket": "green",
    "ticketchng": "green",
    # Wiki
    "backlink": "purple",
    # Forum
    "forumpost": "orange",
    "forumthread": "orange",
    # Other
    "unversioned": "gray",
    "shun": "gray",
    "private": "gray",
    "concealed": "gray",
    "accesslog": "gray",
    "user": "gray",
}

# Regex for validating table names (alphanumerics + underscore, must start with letter or underscore).
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@login_required
def repo_explorer(request, slug):
    """Main schema explorer page -- admin only."""
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, require="admin")

    with reader:
        conn = reader.conn
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        table_names = [row[0] for row in cursor.fetchall()]

        tables = []
        for name in table_names:
            cursor.execute(f"SELECT count(*) FROM [{name}]")  # noqa: S608
            count = cursor.fetchone()[0]
            category = FOSSIL_TABLE_CATEGORIES.get(name, "gray")
            tables.append({"name": name, "count": count, "category": category})

    # Build relationships that involve tables actually present in this repo.
    present = {t["name"] for t in tables}
    relationships = [r for r in FOSSIL_RELATIONSHIPS if r[0] in present and r[1] in present]

    import json

    return render(
        request,
        "fossil/explorer.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "tables": tables,
            "relationships": relationships,
            "relationships_json": json.dumps(relationships),
            "tables_json": json.dumps(tables),
            "active_tab": "explorer",
        },
    )


@login_required
def repo_explorer_table(request, slug, table_name):
    """Return table detail as an HTMX partial -- admin only."""
    project, fossil_repo, reader = _get_repo_and_reader(slug, request, require="admin")

    if not _TABLE_NAME_RE.match(table_name):
        raise Http404("Invalid table name")

    with reader:
        conn = reader.conn
        cursor = conn.cursor()

        # Verify the table exists.
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        if not cursor.fetchone():
            raise Http404("Table not found")

        # Column metadata.
        cursor.execute(f"PRAGMA table_info([{table_name}])")
        columns = [{"cid": row[0], "name": row[1], "type": row[2] or "BLOB", "notnull": row[3], "pk": row[5]} for row in cursor.fetchall()]

        # Paginated rows.
        try:
            page = max(1, int(request.GET.get("page", 1)))
        except (ValueError, TypeError):
            page = 1
        per_page = 25
        offset = (page - 1) * per_page

        cursor.execute(f"SELECT * FROM [{table_name}] LIMIT ? OFFSET ?", (per_page, offset))  # noqa: S608
        col_names = [desc[0] for desc in cursor.description] if cursor.description else []
        raw_rows = cursor.fetchall()

        # Truncate long binary/text values for display.
        rows = []
        for raw in raw_rows:
            display = []
            for cell in raw:
                if isinstance(cell, bytes):
                    display.append(f"<{len(cell)} bytes>")
                elif isinstance(cell, str) and len(cell) > 200:
                    display.append(cell[:200] + "...")
                else:
                    display.append(cell)
            rows.append(display)

        cursor.execute(f"SELECT count(*) FROM [{table_name}]")  # noqa: S608
        total = cursor.fetchone()[0]

    return render(
        request,
        "fossil/partials/explorer_table.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "table_name": table_name,
            "columns": columns,
            "col_names": col_names,
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_next": offset + per_page < total,
            "has_prev": page > 1,
        },
    )


@login_required
def repo_explorer_query(request, slug):
    """Run a custom read-only SQL query against the .fossil file -- admin only."""
    from fossil.ticket_reports import TicketReport

    project, fossil_repo, reader = _get_repo_and_reader(slug, request, require="admin")

    sql = request.GET.get("sql", "").strip()
    results = None
    columns = []
    error = ""

    if sql:
        validation_error = TicketReport.validate_sql(sql)
        if validation_error:
            error = validation_error
        else:
            try:
                with reader:
                    cursor = reader.conn.cursor()
                    cursor.execute(sql)
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    raw = cursor.fetchmany(500)
                    # Truncate long values for display.
                    results = []
                    for raw_row in raw:
                        display = []
                        for cell in raw_row:
                            if isinstance(cell, bytes):
                                display.append(f"<{len(cell)} bytes>")
                            elif isinstance(cell, str) and len(cell) > 200:
                                display.append(cell[:200] + "...")
                            else:
                                display.append(cell)
                        results.append(display)
            except Exception as e:
                error = str(e)

    # Provide table names for the helper sidebar.
    table_names = []
    if not sql or error:
        try:
            with reader:
                cursor = reader.conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                table_names = [row[0] for row in cursor.fetchall()]
        except Exception:
            pass

    return render(
        request,
        "fossil/explorer_query.html",
        {
            "project": project,
            "fossil_repo": fossil_repo,
            "sql": sql,
            "columns": columns,
            "results": results,
            "error": error,
            "table_names": table_names,
            "active_tab": "explorer",
        },
    )

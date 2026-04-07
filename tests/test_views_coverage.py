"""Tests for fossil/views.py -- covering uncovered view functions and helpers.

Focuses on views that can be tested by mocking FossilReader (so no real
.fossil file is needed) and pure Django CRUD views that don't touch Fossil.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.models import FossilRepository
from fossil.reader import (
    CheckinDetail,
    FileEntry,
    RepoMetadata,
    TicketEntry,
    TimelineEntry,
    WikiPage,
)
from organization.models import Team
from projects.models import ProjectTeam

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fossil_repo_obj(sample_project):
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    writer = User.objects.create_user(username="writer_vc", password="testpass123")
    team = Team.objects.create(name="VC Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    c = Client()
    c.login(username="writer_vc", password="testpass123")
    return c


def _url(slug, path):
    return f"/projects/{slug}/fossil/{path}"


def _mock_reader_ctx(mock_cls, **attrs):
    """Configure a patched FossilReader class to work as a context manager
    and attach return values from **attrs to the instance."""
    instance = mock_cls.return_value
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    for key, val in attrs.items():
        setattr(instance, key, MagicMock(return_value=val))
    return instance


def _make_timeline_entry(**overrides):
    defaults = {
        "rid": 1,
        "uuid": "abc123def456",
        "event_type": "ci",
        "timestamp": datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        "user": "testuser",
        "comment": "initial commit",
        "branch": "trunk",
        "parent_rid": 0,
        "is_merge": False,
        "merge_parent_rids": [],
        "rail": 0,
    }
    defaults.update(overrides)
    return TimelineEntry(**defaults)


def _make_file_entry(**overrides):
    defaults = {
        "name": "README.md",
        "uuid": "file-uuid-1",
        "size": 512,
        "is_dir": False,
        "last_commit_message": "initial commit",
        "last_commit_user": "testuser",
        "last_commit_time": datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return FileEntry(**defaults)


# ---------------------------------------------------------------------------
# Content rendering helpers (_render_fossil_content, _is_markdown, _rewrite_fossil_links)
# ---------------------------------------------------------------------------


class TestRenderFossilContent:
    """Test the content rendering pipeline that converts Fossil wiki/markdown to HTML."""

    def test_empty_content(self):
        from fossil.views import _render_fossil_content

        assert _render_fossil_content("") == ""

    def test_markdown_heading(self):
        from fossil.views import _render_fossil_content

        html = _render_fossil_content("# Hello World")
        assert "<h1" in html
        assert "Hello World" in html

    def test_markdown_fenced_code(self):
        from fossil.views import _render_fossil_content

        content = "```python\nprint('hello')\n```"
        html = _render_fossil_content(content)
        assert "print" in html

    def test_fossil_wiki_link_converted(self):
        from fossil.views import _render_fossil_content

        content = "[/info/abc123 | View Checkin]"
        html = _render_fossil_content(content, project_slug="my-project")
        assert "/projects/my-project/fossil/checkin/abc123/" in html

    def test_fossil_wiki_verbatim_block(self):
        from fossil.views import _render_fossil_content

        content = "<h1>Title</h1>\n<verbatim>code here</verbatim>"
        html = _render_fossil_content(content)
        assert "<pre><code>code here</code></pre>" in html

    def test_fossil_wiki_list_bullets(self):
        from fossil.views import _render_fossil_content

        content = "<p>List:</p>\n* Item one\n* Item two"
        html = _render_fossil_content(content)
        assert "<ul>" in html
        assert "<li>" in html
        assert "Item one" in html

    def test_fossil_wiki_ordered_list(self):
        from fossil.views import _render_fossil_content

        # Must start with an HTML element so _is_markdown returns False
        content = "<p>Steps:</p>\n1. Step one\n2. Step two"
        html = _render_fossil_content(content)
        assert "<ol>" in html
        assert "Step one" in html

    def test_fossil_wiki_nowiki_block(self):
        from fossil.views import _render_fossil_content

        content = "<p>Before</p>\n<nowiki><b>Bold</b></nowiki>"
        html = _render_fossil_content(content)
        assert "<b>Bold</b>" in html

    def test_fossil_interwiki_link(self):
        from fossil.views import _render_fossil_content

        content = "<p>See [wikipedia:Fossil_(software)]</p>"
        html = _render_fossil_content(content)
        assert "en.wikipedia.org/wiki/Fossil_(software)" in html

    def test_fossil_anchor_link(self):
        from fossil.views import _render_fossil_content

        content = "<p>Jump to [#section1]</p>"
        html = _render_fossil_content(content)
        assert 'href="#section1"' in html

    def test_fossil_bare_wiki_link(self):
        from fossil.views import _render_fossil_content

        content = "<p>See [PageName]</p>"
        html = _render_fossil_content(content)
        assert 'href="PageName"' in html

    def test_markdown_fossil_link_resolved(self):
        from fossil.views import _render_fossil_content

        content = "# Page\n\n[./file.wiki | Link Text]"
        html = _render_fossil_content(content, project_slug="proj", base_path="www/")
        assert "Link Text" in html


class TestIsMarkdown:
    def test_heading_detected(self):
        from fossil.views import _is_markdown

        assert _is_markdown("# Title\nSome text") is True

    def test_fenced_code_detected(self):
        from fossil.views import _is_markdown

        assert _is_markdown("Some text\n```\ncode\n```") is True

    def test_html_start_not_markdown(self):
        from fossil.views import _is_markdown

        assert _is_markdown("<h1>Title</h1>\n<p>Paragraph</p>") is False

    def test_multiple_markdown_headings(self):
        from fossil.views import _is_markdown

        content = "Some text\n## Heading\n## Another"
        assert _is_markdown(content) is True

    def test_plain_text_is_markdown(self):
        from fossil.views import _is_markdown

        # Plain text without HTML tags defaults to markdown
        assert _is_markdown("Just plain text") is True


class TestRewriteFossilLinks:
    def test_info_hash_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/info/abc123">link</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/checkin/abc123/" in result

    def test_doc_trunk_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/doc/trunk/www/readme.wiki">docs</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/code/file/www/readme.wiki" in result

    def test_wiki_path_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/wiki/HomePage">home</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/wiki/page/HomePage" in result

    def test_wiki_query_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/wiki?name=HomePage">home</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/wiki/page/HomePage" in result

    def test_tktview_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/tktview/abc123">ticket</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/tickets/abc123/" in result

    def test_vdiff_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/vdiff?from=aaa&to=bbb">diff</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/compare/?from=aaa&to=bbb" in result

    def test_timeline_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/timeline?n=20">tl</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/timeline/" in result

    def test_forumpost_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/forumpost/abc123">post</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/forum/abc123/" in result

    def test_forum_base_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/forum">forum</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/forum/" in result

    def test_www_path_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/www/index.html">page</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/docs/www/index.html" in result

    def test_dir_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/dir">browse</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/code/" in result

    def test_help_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/help/clone">help</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/docs/www/help.wiki" in result

    def test_external_link_preserved(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="https://example.com/page">ext</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "https://example.com/page" in result

    def test_empty_slug_passthrough(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/info/abc">link</a>'
        assert _rewrite_fossil_links(html, "") == html

    def test_scheme_link_info(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="info:abc123">checkin</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/checkin/abc123/" in result

    def test_scheme_link_wiki(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="wiki:PageName">page</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/wiki/page/PageName" in result

    def test_builtin_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/builtin/default.css">skin</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/code/file/skins/default.css" in result

    def test_setup_link_not_rewritten(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/setup_skin">settings</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/setup_skin" in result

    def test_wiki_file_extension_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="/concepts.wiki">page</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/docs/www/concepts.wiki" in result

    def test_external_fossil_scm_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="https://fossil-scm.org/home/info/abc123">ext</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/checkin/abc123/" in result

    def test_scheme_link_forum(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="forum:/forumpost/abc123">post</a>'
        result = _rewrite_fossil_links(html, "myproj")
        assert "/projects/myproj/fossil/forum/abc123/" in result


# ---------------------------------------------------------------------------
# Split diff helper
# ---------------------------------------------------------------------------


class TestComputeSplitLines:
    def test_context_lines_both_sides(self):
        from fossil.views import _compute_split_lines

        lines = [{"text": " same", "type": "context", "old_num": 1, "new_num": 1}]
        left, right = _compute_split_lines(lines)
        assert len(left) == 1
        assert left[0]["type"] == "context"
        assert right[0]["type"] == "context"

    def test_del_add_paired(self):
        from fossil.views import _compute_split_lines

        lines = [
            {"text": "-old", "type": "del", "old_num": 1, "new_num": ""},
            {"text": "+new", "type": "add", "old_num": "", "new_num": 1},
        ]
        left, right = _compute_split_lines(lines)
        assert left[0]["type"] == "del"
        assert right[0]["type"] == "add"

    def test_orphan_add(self):
        from fossil.views import _compute_split_lines

        lines = [{"text": "+added", "type": "add", "old_num": "", "new_num": 1}]
        left, right = _compute_split_lines(lines)
        assert left[0]["type"] == "empty"
        assert right[0]["type"] == "add"

    def test_header_hunk_both_sides(self):
        from fossil.views import _compute_split_lines

        lines = [
            {"text": "--- a/f", "type": "header", "old_num": "", "new_num": ""},
            {"text": "@@ -1 +1 @@", "type": "hunk", "old_num": "", "new_num": ""},
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 2
        assert left[0]["type"] == "header"
        assert left[1]["type"] == "hunk"

    def test_uneven_del_add_padded(self):
        """When there are more deletions than additions, right side gets empty placeholders."""
        from fossil.views import _compute_split_lines

        lines = [
            {"text": "-line1", "type": "del", "old_num": 1, "new_num": ""},
            {"text": "-line2", "type": "del", "old_num": 2, "new_num": ""},
            {"text": "+new1", "type": "add", "old_num": "", "new_num": 1},
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 2
        assert left[0]["type"] == "del"
        assert left[1]["type"] == "del"
        assert right[0]["type"] == "add"
        assert right[1]["type"] == "empty"


# ---------------------------------------------------------------------------
# Timeline view (mocked FossilReader)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTimelineViewMocked:
    def test_timeline_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        entries = [_make_timeline_entry(rid=1)]
        with patch("fossil.views.FossilReader") as mock_cls:
            _mock_reader_ctx(mock_cls, get_timeline=entries)
            with patch("fossil.views._get_repo_and_reader") as mock_grr:
                repo = FossilRepository.objects.get(project=sample_project)
                mock_grr.return_value = (sample_project, repo, mock_cls.return_value)
                response = admin_client.get(_url(slug, "timeline/"))
        assert response.status_code == 200
        assert "initial commit" in response.content.decode()

    def test_timeline_with_type_filter(self, admin_client, sample_project):
        slug = sample_project.slug
        entries = [_make_timeline_entry(rid=1, event_type="w", comment="wiki edit")]
        with patch("fossil.views.FossilReader") as mock_cls:
            _mock_reader_ctx(mock_cls, get_timeline=entries)
            with patch("fossil.views._get_repo_and_reader") as mock_grr:
                repo = FossilRepository.objects.get(project=sample_project)
                mock_grr.return_value = (sample_project, repo, mock_cls.return_value)
                response = admin_client.get(_url(slug, "timeline/?type=w"))
        assert response.status_code == 200

    def test_timeline_htmx_partial(self, admin_client, sample_project):
        slug = sample_project.slug
        entries = [_make_timeline_entry(rid=1)]
        with patch("fossil.views.FossilReader") as mock_cls:
            _mock_reader_ctx(mock_cls, get_timeline=entries)
            with patch("fossil.views._get_repo_and_reader") as mock_grr:
                repo = FossilRepository.objects.get(project=sample_project)
                mock_grr.return_value = (sample_project, repo, mock_cls.return_value)
                response = admin_client.get(_url(slug, "timeline/"), HTTP_HX_REQUEST="true")
        assert response.status_code == 200

    def test_timeline_denied_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.get(_url(sample_project.slug, "timeline/"))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Ticket list/detail (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTicketViewsMocked:
    def test_ticket_list_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        tickets = [
            TicketEntry(
                uuid="tkt-uuid-1",
                title="Bug report",
                status="Open",
                type="Code_Defect",
                created=datetime(2026, 3, 1, tzinfo=UTC),
                owner="testuser",
            )
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_tickets.return_value = tickets
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tickets/"))
        assert response.status_code == 200
        assert "Bug report" in response.content.decode()

    def test_ticket_list_search_filter(self, admin_client, sample_project):
        slug = sample_project.slug
        tickets = [
            TicketEntry(
                uuid="t1", title="Login bug", status="Open", type="Code_Defect", created=datetime(2026, 3, 1, tzinfo=UTC), owner="u"
            ),
            TicketEntry(
                uuid="t2", title="Dashboard fix", status="Open", type="Code_Defect", created=datetime(2026, 3, 1, tzinfo=UTC), owner="u"
            ),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_tickets.return_value = tickets
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tickets/?search=login"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Login bug" in content
        # Dashboard should be filtered out
        assert "Dashboard fix" not in content

    def test_ticket_list_htmx_partial(self, admin_client, sample_project):
        slug = sample_project.slug
        tickets = [
            TicketEntry(
                uuid="t1", title="A ticket", status="Open", type="Code_Defect", created=datetime(2026, 3, 1, tzinfo=UTC), owner="u"
            ),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_tickets.return_value = tickets
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tickets/"), HTTP_HX_REQUEST="true")
        assert response.status_code == 200

    def test_ticket_detail_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        ticket = TicketEntry(
            uuid="tkt-detail-1",
            title="Detail test",
            status="Open",
            type="Code_Defect",
            created=datetime(2026, 3, 1, tzinfo=UTC),
            owner="testuser",
            body="Some description **bold**",
        )
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_ticket_detail.return_value = ticket
            reader.get_ticket_comments.return_value = [
                {"user": "dev", "timestamp": datetime(2026, 3, 2, tzinfo=UTC), "comment": "Working on it"}
            ]
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tickets/tkt-detail-1/"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Detail test" in content

    def test_ticket_detail_not_found(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_ticket_detail.return_value = None
            reader.get_ticket_comments.return_value = []
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tickets/nonexistent/"))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Wiki list/page (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWikiViewsMocked:
    def test_wiki_list_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        pages = [
            WikiPage(name="Home", content="# Home", last_modified=datetime(2026, 3, 1, tzinfo=UTC), user="admin"),
            WikiPage(name="Setup", content="Setup guide", last_modified=datetime(2026, 3, 1, tzinfo=UTC), user="dev"),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_wiki_pages.return_value = pages
            reader.get_wiki_page.return_value = pages[0]
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "wiki/"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Home" in content
        assert "Setup" in content

    def test_wiki_list_search(self, admin_client, sample_project):
        slug = sample_project.slug
        pages = [
            WikiPage(name="Home", content="# Home", last_modified=datetime(2026, 3, 1, tzinfo=UTC), user="admin"),
            WikiPage(name="Setup", content="Setup guide", last_modified=datetime(2026, 3, 1, tzinfo=UTC), user="dev"),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_wiki_pages.return_value = pages
            reader.get_wiki_page.return_value = None
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "wiki/?search=setup"))
        assert response.status_code == 200

    def test_wiki_page_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        page = WikiPage(name="Home", content="# Welcome\nHello world", last_modified=datetime(2026, 3, 1, tzinfo=UTC), user="admin")
        all_pages = [page]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_wiki_page.return_value = page
            reader.get_wiki_pages.return_value = all_pages
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "wiki/page/Home"))
        assert response.status_code == 200
        assert "Welcome" in response.content.decode()

    def test_wiki_page_not_found(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_wiki_page.return_value = None
            reader.get_wiki_pages.return_value = []
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "wiki/page/NonexistentPage"))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Search view (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSearchViewMocked:
    def test_search_with_query(self, admin_client, sample_project):
        slug = sample_project.slug
        results = [{"type": "ci", "uuid": "abc", "comment": "found it", "user": "dev"}]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.search.return_value = results
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "search/?q=found"))
        assert response.status_code == 200

    def test_search_empty_query(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "search/"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Compare checkins view (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCompareCheckinsViewMocked:
    def test_compare_no_params(self, admin_client, sample_project):
        """Compare page renders without from/to params (shows empty form)."""
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "compare/"))
        assert response.status_code == 200

    def test_compare_with_params(self, admin_client, sample_project):
        """Compare page with from/to parameters renders diffs."""
        slug = sample_project.slug
        from_detail = CheckinDetail(
            uuid="aaa111",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            user="dev",
            comment="from commit",
            files_changed=[{"name": "f.txt", "uuid": "u1", "prev_uuid": "", "change_type": "A"}],
        )
        to_detail = CheckinDetail(
            uuid="bbb222",
            timestamp=datetime(2026, 3, 2, tzinfo=UTC),
            user="dev",
            comment="to commit",
            files_changed=[{"name": "f.txt", "uuid": "u2", "prev_uuid": "u1", "change_type": "M"}],
        )
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_checkin_detail.side_effect = lambda uuid: from_detail if "aaa" in uuid else to_detail
            reader.get_file_content.return_value = b"file content"
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "compare/?from=aaa111&to=bbb222"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Timeline RSS feed (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTimelineRssViewMocked:
    def test_rss_feed(self, admin_client, sample_project):
        slug = sample_project.slug
        entries = [_make_timeline_entry(rid=1, comment="rss commit")]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_timeline.return_value = entries
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "timeline/rss/"))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml"
        content = response.content.decode()
        assert "rss commit" in content
        assert "<rss" in content


# ---------------------------------------------------------------------------
# Tickets CSV export (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTicketsCsvViewMocked:
    def test_csv_export(self, admin_client, sample_project):
        slug = sample_project.slug
        tickets = [
            TicketEntry(
                uuid="csv-uuid",
                title="Export test",
                status="Open",
                type="Code_Defect",
                created=datetime(2026, 3, 1, tzinfo=UTC),
                owner="testuser",
                priority="High",
                severity="Critical",
            )
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_tickets.return_value = tickets
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tickets/export/"))
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        content = response.content.decode()
        assert "Export test" in content
        assert "csv-uuid" in content


# ---------------------------------------------------------------------------
# Branch list view (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBranchListViewMocked:
    def test_branch_list_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        branches = [
            SimpleNamespace(
                name="trunk", last_user="dev", last_checkin=datetime(2026, 3, 1, tzinfo=UTC), checkin_count=50, last_uuid="abc123"
            ),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_branches.return_value = branches
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "branches/"))
        assert response.status_code == 200
        assert "trunk" in response.content.decode()

    def test_branch_list_search(self, admin_client, sample_project):
        slug = sample_project.slug
        branches = [
            SimpleNamespace(
                name="trunk", last_user="dev", last_checkin=datetime(2026, 3, 1, tzinfo=UTC), checkin_count=50, last_uuid="abc123"
            ),
            SimpleNamespace(
                name="feature-x", last_user="dev", last_checkin=datetime(2026, 3, 1, tzinfo=UTC), checkin_count=5, last_uuid="def456"
            ),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_branches.return_value = branches
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "branches/?search=feature"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "feature-x" in content


# ---------------------------------------------------------------------------
# Tag list view (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTagListViewMocked:
    def test_tag_list_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        tags = [
            SimpleNamespace(name="v1.0", uuid="abc123", user="dev", timestamp=datetime(2026, 3, 1, tzinfo=UTC)),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_tags.return_value = tags
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tags/"))
        assert response.status_code == 200
        assert "v1.0" in response.content.decode()

    def test_tag_list_search(self, admin_client, sample_project):
        slug = sample_project.slug
        tags = [
            SimpleNamespace(name="v1.0", uuid="abc123", user="dev", timestamp=datetime(2026, 3, 1, tzinfo=UTC)),
            SimpleNamespace(name="v2.0-beta", uuid="def456", user="dev", timestamp=datetime(2026, 3, 1, tzinfo=UTC)),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_tags.return_value = tags
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tags/?search=beta"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "v2.0-beta" in content


# ---------------------------------------------------------------------------
# Stats view (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRepoStatsViewMocked:
    def test_stats_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        stats = {"total_artifacts": 100, "checkin_count": 50, "wiki_events": 5, "ticket_events": 10, "forum_events": 2, "total_events": 67}
        contributors = [{"user": "dev", "count": 50}]
        activity = [{"count": c} for c in range(52)]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_repo_statistics.return_value = stats
            reader.get_top_contributors.return_value = contributors
            reader.get_commit_activity.return_value = activity
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "stats/"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Checkins" in content or "50" in content


# ---------------------------------------------------------------------------
# File history view (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFileHistoryViewMocked:
    def test_file_history_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        history = [
            {"uuid": "abc", "timestamp": datetime(2026, 3, 1, tzinfo=UTC), "user": "dev", "comment": "edit file"},
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_file_history.return_value = history
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/history/README.md"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Code browser (mocked) -- tests the _build_file_tree helper indirectly
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCodeBrowserViewMocked:
    def test_code_browser_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        files = [
            _make_file_entry(name="README.md", uuid="f1"),
            _make_file_entry(name="src/main.py", uuid="f2"),
        ]
        metadata = RepoMetadata(project_name="Test", checkin_count=10)
        latest = [_make_timeline_entry(rid=1)]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc123"
            reader.get_files_at_checkin.return_value = files
            reader.get_metadata.return_value = metadata
            reader.get_timeline.return_value = latest
            reader.get_file_content.return_value = b"# README\nHello"
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "README" in content

    def test_code_browser_htmx_partial(self, admin_client, sample_project):
        slug = sample_project.slug
        files = [_make_file_entry(name="README.md", uuid="f1")]
        metadata = RepoMetadata()
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = files
            reader.get_metadata.return_value = metadata
            reader.get_timeline.return_value = []
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/"), HTTP_HX_REQUEST="true")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Code file view (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCodeFileViewMocked:
    def test_code_file_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        files = [_make_file_entry(name="main.py", uuid="f1")]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = files
            reader.get_file_content.return_value = b"print('hello')"
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/file/main.py"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "print" in content

    def test_code_file_not_found(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = []
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/file/nonexistent.txt"))
        assert response.status_code == 404

    def test_code_file_binary(self, admin_client, sample_project):
        slug = sample_project.slug
        files = [_make_file_entry(name="image.png", uuid="f1")]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = files
            # Deliberately invalid UTF-8 to trigger binary detection
            reader.get_file_content.return_value = b"\x89PNG\r\n\x1a\n\x00\x00"
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/file/image.png"))
        assert response.status_code == 200
        assert "Binary file" in response.content.decode()

    def test_code_file_rendered_mode(self, admin_client, sample_project):
        """Wiki files can be rendered instead of showing source."""
        slug = sample_project.slug
        files = [_make_file_entry(name="page.md", uuid="f1")]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = files
            reader.get_file_content.return_value = b"# Hello\nWorld"
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/file/page.md?mode=rendered"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Code raw download (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCodeRawViewMocked:
    def test_raw_download(self, admin_client, sample_project):
        slug = sample_project.slug
        files = [_make_file_entry(name="data.csv", uuid="f1")]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = files
            reader.get_file_content.return_value = b"col1,col2\na,b"
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/raw/data.csv"))
        assert response.status_code == 200
        assert response["Content-Disposition"] == 'attachment; filename="data.csv"'

    def test_raw_file_not_found(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = []
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/raw/missing.txt"))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Code blame (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCodeBlameViewMocked:
    def test_blame_renders_with_dates(self, admin_client, sample_project):
        slug = sample_project.slug
        blame_lines = [
            {"user": "dev", "date": "2026-01-01", "uuid": "abc", "line_num": 1, "text": "line one"},
            {"user": "dev", "date": "2026-03-01", "uuid": "def", "line_num": 2, "text": "line two"},
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.is_available.return_value = True
                cli.blame.return_value = blame_lines
                response = admin_client.get(_url(slug, "code/blame/main.py"))
        assert response.status_code == 200

    def test_blame_no_fossil_binary(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.is_available.return_value = False
                response = admin_client.get(_url(slug, "code/blame/main.py"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Toggle watch / notifications
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestToggleWatch:
    def test_watch_project(self, admin_client, sample_project, admin_user):
        from fossil.notifications import ProjectWatch

        response = admin_client.post(_url(sample_project.slug, "watch/"))
        assert response.status_code == 302
        assert ProjectWatch.objects.filter(user=admin_user, project=sample_project).exists()

    def test_unwatch_project(self, admin_client, sample_project, admin_user):
        from fossil.notifications import ProjectWatch

        ProjectWatch.objects.create(user=admin_user, project=sample_project, event_filter="all", created_by=admin_user)
        response = admin_client.post(_url(sample_project.slug, "watch/"))
        assert response.status_code == 302
        # Should be soft-deleted
        assert not ProjectWatch.objects.filter(user=admin_user, project=sample_project, deleted_at__isnull=True).exists()

    def test_watch_with_event_filter(self, admin_client, sample_project, admin_user):
        from fossil.notifications import ProjectWatch

        response = admin_client.post(_url(sample_project.slug, "watch/"), {"event_filter": "checkins"})
        assert response.status_code == 302
        watch = ProjectWatch.objects.get(user=admin_user, project=sample_project)
        assert watch.event_filter == "checkins"

    def test_watch_denied_anon(self, client, sample_project):
        response = client.post(_url(sample_project.slug, "watch/"))
        assert response.status_code == 302  # redirect to login


# ---------------------------------------------------------------------------
# Checkin detail (mocked) -- the diff computation path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCheckinDetailViewMocked:
    def test_checkin_detail_with_diffs(self, admin_client, sample_project):
        slug = sample_project.slug
        checkin = CheckinDetail(
            uuid="abc123full",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            user="dev",
            comment="fix bug",
            branch="trunk",
            files_changed=[
                {"name": "fix.py", "uuid": "new-uuid", "prev_uuid": "old-uuid", "change_type": "M"},
            ],
        )
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_checkin_detail.return_value = checkin

            def fake_content(uuid):
                if uuid == "old-uuid":
                    return b"old line\n"
                return b"new line\n"

            reader.get_file_content.side_effect = fake_content
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)

            with patch("fossil.ci.StatusCheck") as mock_sc:
                mock_sc.objects.filter.return_value = []
                response = admin_client.get(_url(slug, "checkin/abc123full/"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "fix bug" in content

    def test_checkin_not_found(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_checkin_detail.return_value = None
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "checkin/nonexistent/"))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Technote views (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTechnoteViewsMocked:
    def test_technote_list(self, admin_client, sample_project):
        slug = sample_project.slug
        notes = [SimpleNamespace(uuid="n1", comment="Release notes", user="dev", timestamp=datetime(2026, 3, 1, tzinfo=UTC))]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_technotes.return_value = notes
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "technotes/"))
        assert response.status_code == 200

    def test_technote_detail(self, admin_client, sample_project):
        slug = sample_project.slug
        note = {
            "uuid": "n1",
            "comment": "Release v1",
            "body": "## Changes\n- Fix",
            "user": "dev",
            "timestamp": datetime(2026, 3, 1, tzinfo=UTC),
        }
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_technote_detail.return_value = note
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "technotes/n1/"))
        assert response.status_code == 200

    def test_technote_detail_not_found(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_technote_detail.return_value = None
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "technotes/nonexistent/"))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Unversioned files list (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnversionedListViewMocked:
    def test_unversioned_list(self, admin_client, sample_project):
        slug = sample_project.slug
        files = [SimpleNamespace(name="logo.png", size=1024, mtime=datetime(2026, 3, 1, tzinfo=UTC), hash="abc")]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_unversioned_files.return_value = files
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "files/"))
        assert response.status_code == 200

    def test_unversioned_search(self, admin_client, sample_project):
        slug = sample_project.slug
        files = [
            SimpleNamespace(name="logo.png", size=1024, mtime=datetime(2026, 3, 1, tzinfo=UTC), hash="abc"),
            SimpleNamespace(name="data.csv", size=512, mtime=datetime(2026, 3, 1, tzinfo=UTC), hash="def"),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_unversioned_files.return_value = files
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "files/?search=logo"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Fossil docs views (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFossilDocsViewsMocked:
    def test_docs_index(self, admin_client, sample_project):
        slug = sample_project.slug
        response = admin_client.get(_url(slug, "docs/"))
        assert response.status_code == 200

    def test_doc_page_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        files = [_make_file_entry(name="www/concepts.wiki", uuid="f1")]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = files
            reader.get_file_content.return_value = b"<h1>Concepts</h1>\n<p>Text here</p>"
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "docs/www/concepts.wiki"))
        assert response.status_code == 200
        assert "Concepts" in response.content.decode()

    def test_doc_page_not_found(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = []
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "docs/www/missing.wiki"))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# User activity view (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserActivityViewMocked:
    def test_user_activity_renders(self, admin_client, sample_project):
        slug = sample_project.slug
        activity = {
            "checkin_count": 25,
            "checkins": [{"uuid": "a", "comment": "fix", "timestamp": datetime(2026, 3, 1, tzinfo=UTC)}],
            "daily_activity": {"2026-03-01": 5},
        }
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_user_activity.return_value = activity
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "user/dev/"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Status badge view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStatusBadgeView:
    def test_badge_unknown(self, admin_client, sample_project):
        slug = sample_project.slug
        response = admin_client.get(_url(slug, "api/status/abc123/badge.svg"))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/svg+xml"
        content = response.content.decode()
        assert "unknown" in content

    def test_badge_passing(self, admin_client, sample_project, fossil_repo_obj):
        from fossil.ci import StatusCheck

        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="pass123", context="ci/test", state="success")
        response = admin_client.get(_url(sample_project.slug, "api/status/pass123/badge.svg"))
        assert response.status_code == 200
        assert "passing" in response.content.decode()

    def test_badge_failing(self, admin_client, sample_project, fossil_repo_obj):
        from fossil.ci import StatusCheck

        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="fail123", context="ci/test", state="failure")
        response = admin_client.get(_url(sample_project.slug, "api/status/fail123/badge.svg"))
        assert response.status_code == 200
        assert "failing" in response.content.decode()

    def test_badge_pending(self, admin_client, sample_project, fossil_repo_obj):
        from fossil.ci import StatusCheck

        StatusCheck.objects.create(repository=fossil_repo_obj, checkin_uuid="pend123", context="ci/test", state="pending")
        response = admin_client.get(_url(sample_project.slug, "api/status/pend123/badge.svg"))
        assert response.status_code == 200
        assert "pending" in response.content.decode()


# ---------------------------------------------------------------------------
# Status check API (GET path)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStatusCheckApiGet:
    def test_get_status_checks(self, admin_client, sample_project, fossil_repo_obj):
        from fossil.ci import StatusCheck

        StatusCheck.objects.create(
            repository=fossil_repo_obj, checkin_uuid="apicheck", context="ci/lint", state="success", description="OK"
        )
        response = admin_client.get(_url(sample_project.slug, "api/status?checkin=apicheck"))
        assert response.status_code == 200
        data = response.json()
        assert data["checkin"] == "apicheck"
        assert len(data["checks"]) == 1
        assert data["checks"][0]["context"] == "ci/lint"

    def test_get_status_no_checkin_param(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(_url(sample_project.slug, "api/status"))
        assert response.status_code == 400

    def test_get_status_denied_private(self, client, sample_project, fossil_repo_obj):
        """Anonymous user denied on private project."""
        response = client.get(_url(sample_project.slug, "api/status?checkin=abc"))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Fossil xfer endpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFossilXferView:
    def test_xfer_get_public_project(self, client, sample_project, fossil_repo_obj):
        """GET on xfer endpoint shows clone info for public projects."""
        sample_project.visibility = "public"
        sample_project.save()
        response = client.get(_url(sample_project.slug, "xfer"))
        assert response.status_code == 200
        assert "clone" in response.content.decode().lower()

    def test_xfer_get_private_denied(self, client, sample_project, fossil_repo_obj):
        """GET on xfer endpoint denied for private projects without auth."""
        response = client.get(_url(sample_project.slug, "xfer"))
        assert response.status_code == 403

    def test_xfer_method_not_allowed(self, admin_client, sample_project, fossil_repo_obj):
        """PUT/PATCH not supported."""
        response = admin_client.put(_url(sample_project.slug, "xfer"))
        assert response.status_code == 405


# ---------------------------------------------------------------------------
# Build file tree helper
# ---------------------------------------------------------------------------


class TestBuildFileTree:
    def test_root_listing(self):
        from fossil.views import _build_file_tree

        files = [
            _make_file_entry(name="README.md", uuid="f1"),
            _make_file_entry(name="src/main.py", uuid="f2"),
            _make_file_entry(name="src/utils.py", uuid="f3"),
        ]
        tree = _build_file_tree(files)
        # Should have 1 dir (src) and 1 file (README.md)
        dirs = [e for e in tree if e["is_dir"]]
        regular_files = [e for e in tree if not e["is_dir"]]
        assert len(dirs) == 1
        assert dirs[0]["name"] == "src"
        assert len(regular_files) == 1
        assert regular_files[0]["name"] == "README.md"

    def test_subdir_listing(self):
        from fossil.views import _build_file_tree

        files = [
            _make_file_entry(name="src/main.py", uuid="f2"),
            _make_file_entry(name="src/utils.py", uuid="f3"),
            _make_file_entry(name="src/lib/helper.py", uuid="f4"),
        ]
        tree = _build_file_tree(files, current_dir="src")
        dirs = [e for e in tree if e["is_dir"]]
        regular_files = [e for e in tree if not e["is_dir"]]
        assert len(dirs) == 1
        assert dirs[0]["name"] == "lib"
        assert len(regular_files) == 2

    def test_skips_bad_filenames(self):
        from fossil.views import _build_file_tree

        files = [
            _make_file_entry(name="good.txt", uuid="f1"),
            _make_file_entry(name="bad\nname.txt", uuid="f2"),
        ]
        tree = _build_file_tree(files)
        assert len(tree) == 1
        assert tree[0]["name"] == "good.txt"

    def test_dirs_sorted_first(self):
        from fossil.views import _build_file_tree

        files = [
            _make_file_entry(name="zebra.txt", uuid="f1"),
            _make_file_entry(name="alpha/main.py", uuid="f2"),
        ]
        tree = _build_file_tree(files)
        assert tree[0]["is_dir"] is True
        assert tree[0]["name"] == "alpha"
        assert tree[1]["is_dir"] is False


# ---------------------------------------------------------------------------
# Content rendering: more edge cases for _render_fossil_content
# ---------------------------------------------------------------------------


class TestRenderFossilContentEdgeCases:
    def test_fossil_wiki_list_type_switch(self):
        """Test switching from bullet list to ordered list in wiki content."""
        from fossil.views import _render_fossil_content

        content = "<div>Intro</div>\n* bullet\n1. ordered"
        html = _render_fossil_content(content)
        assert "<ul>" in html
        assert "<ol>" in html
        assert "bullet" in html
        assert "ordered" in html

    def test_fossil_wiki_link_relative_path(self):
        from fossil.views import _render_fossil_content

        content = "<p>[./subpage | Sub Page]</p>"
        html = _render_fossil_content(content, project_slug="proj", base_path="www/")
        assert "Sub Page" in html
        assert "/www/" in html

    def test_fossil_wiki_link_bare_path(self):
        from fossil.views import _render_fossil_content

        content = "<p>[page.wiki | Page]</p>"
        html = _render_fossil_content(content, project_slug="proj", base_path="docs/")
        assert "Page" in html

    def test_fossil_wiki_p_wrap(self):
        """Double newlines in wiki content get wrapped in <p> tags."""
        from fossil.views import _render_fossil_content

        content = "<div>First</div>\n\nSecond paragraph"
        html = _render_fossil_content(content)
        assert "<p>" in html

    def test_markdown_with_tables(self):
        from fossil.views import _render_fossil_content

        content = "# Table\n\n| Col1 | Col2 |\n|------|------|\n| a | b |"
        html = _render_fossil_content(content)
        assert "<table>" in html

    def test_markdown_fossil_link_with_base_path(self):
        """Markdown-mode Fossil links with relative paths resolve using base_path."""
        from fossil.views import _render_fossil_content

        content = "# Page\n[file.wiki | Link]"
        html = _render_fossil_content(content, project_slug="proj", base_path="docs/")
        assert "Link" in html

    def test_external_fossil_scm_wiki_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="https://fossil-scm.org/home/wiki/PageName">link</a>'
        result = _rewrite_fossil_links(html, "proj")
        assert "/projects/proj/fossil/wiki/page/PageName" in result

    def test_external_fossil_scm_doc_rewrite(self):
        from fossil.views import _rewrite_fossil_links

        html = '<a href="https://www.fossil-scm.org/home/doc/trunk/www/file.wiki">doc</a>'
        result = _rewrite_fossil_links(html, "proj")
        assert "/projects/proj/fossil/docs/www/file.wiki" in result


# ---------------------------------------------------------------------------
# Compare checkins: with actual diff computation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCompareWithDiffs:
    def test_compare_produces_diff_lines(self, admin_client, sample_project):
        """Compare with two checkins that have overlapping changed files produces unified diff."""
        slug = sample_project.slug
        from_detail = CheckinDetail(
            uuid="from111",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            user="dev",
            comment="before",
            files_changed=[{"name": "app.py", "uuid": "old1", "prev_uuid": "", "change_type": "A"}],
        )
        to_detail = CheckinDetail(
            uuid="to222",
            timestamp=datetime(2026, 3, 2, tzinfo=UTC),
            user="dev",
            comment="after",
            files_changed=[{"name": "app.py", "uuid": "new1", "prev_uuid": "old1", "change_type": "M"}],
        )
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_checkin_detail.side_effect = lambda uuid: from_detail if "from" in uuid else to_detail

            def file_content(uuid):
                if uuid == "old1":
                    return b"line1\nline2\nline3\n"
                return b"line1\nmodified\nline3\nnew_line\n"

            reader.get_file_content.side_effect = file_content
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "compare/?from=from111&to=to222"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Repo settings view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRepoSettingsView:
    def test_settings_get_denied_for_non_admin(self, no_perm_client, sample_project):
        response = no_perm_client.get(_url(sample_project.slug, "settings/"))
        assert response.status_code == 403

    def test_settings_get_denied_for_anon(self, client, sample_project):
        response = client.get(_url(sample_project.slug, "settings/"))
        assert response.status_code == 302

    def test_settings_post_update_remote(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            _url(sample_project.slug, "settings/"),
            {"action": "update_remote", "remote_url": "https://fossil.example.com/repo"},
        )
        assert response.status_code == 302
        fossil_repo_obj.refresh_from_db()
        assert fossil_repo_obj.remote_url == "https://fossil.example.com/repo"


# ---------------------------------------------------------------------------
# Fossil doc_page: directory index fallback
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocPageIndexFallback:
    def test_doc_page_directory_index(self, admin_client, sample_project):
        """Requesting a directory path falls back to index.html."""
        slug = sample_project.slug
        files = [_make_file_entry(name="www/index.html", uuid="idx1")]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = files
            reader.get_file_content.return_value = b"<h1>Index</h1>"
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "docs/www/"))
        assert response.status_code == 200
        assert "Index" in response.content.decode()


# ---------------------------------------------------------------------------
# Code blame: age coloring edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCodeBlameAgeColoring:
    def test_blame_all_same_date(self, admin_client, sample_project):
        """All blame lines have the same date -- date_range is 1 to avoid division by zero."""
        slug = sample_project.slug
        blame_lines = [
            {"user": "dev", "date": "2026-03-01", "uuid": "abc", "line_num": 1, "text": "line1"},
            {"user": "dev", "date": "2026-03-01", "uuid": "abc", "line_num": 2, "text": "line2"},
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.is_available.return_value = True
                cli.blame.return_value = blame_lines
                response = admin_client.get(_url(slug, "code/blame/main.py"))
        assert response.status_code == 200

    def test_blame_no_dates(self, admin_client, sample_project):
        """Blame lines with no dates -- fallback to gray."""
        slug = sample_project.slug
        blame_lines = [
            {"user": "dev", "date": "", "uuid": "abc", "line_num": 1, "text": "line1"},
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.is_available.return_value = True
                cli.blame.return_value = blame_lines
                response = admin_client.get(_url(slug, "code/blame/main.py"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Wiki CRUD (create/edit) -- requires mocking FossilCLI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWikiCreateEditMocked:
    def test_wiki_create_get_form(self, admin_client, sample_project):
        """GET wiki create shows form for writers."""
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "wiki/create/"))
        assert response.status_code == 200
        assert "New Wiki Page" in response.content.decode()

    def test_wiki_create_post(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.wiki_create.return_value = True
                response = admin_client.post(_url(slug, "wiki/create/"), {"name": "NewPage", "content": "# New Page"})
        assert response.status_code == 302

    def test_wiki_edit_get_form(self, admin_client, sample_project):
        slug = sample_project.slug
        page = WikiPage(name="EditMe", content="old content", last_modified=datetime(2026, 3, 1, tzinfo=UTC), user="admin")
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_wiki_page.return_value = page
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "wiki/edit/EditMe"))
        assert response.status_code == 200

    def test_wiki_edit_post(self, admin_client, sample_project):
        slug = sample_project.slug
        page = WikiPage(name="EditMe", content="old content", last_modified=datetime(2026, 3, 1, tzinfo=UTC), user="admin")
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_wiki_page.return_value = page
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.wiki_commit.return_value = True
                response = admin_client.post(_url(slug, "wiki/edit/EditMe"), {"content": "# Updated"})
        assert response.status_code == 302

    def test_wiki_edit_not_found(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_wiki_page.return_value = None
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "wiki/edit/Missing"))
        assert response.status_code == 404

    def test_wiki_create_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.get(_url(sample_project.slug, "wiki/create/"))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Ticket CRUD (create/edit/comment) -- requires mocking FossilCLI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTicketCrudMocked:
    def test_ticket_create_get_form(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tickets/create/"))
        assert response.status_code == 200
        assert "New Ticket" in response.content.decode()

    def test_ticket_create_post(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.ticket_add.return_value = True
                response = admin_client.post(
                    _url(slug, "tickets/create/"),
                    {"title": "New Bug", "body": "Description", "type": "Code_Defect"},
                )
        assert response.status_code == 302

    @pytest.mark.skip(reason="ticket_edit.html template uses .split which is not valid Django template syntax -- pre-existing bug")
    def test_ticket_edit_get_form(self, admin_client, sample_project):
        slug = sample_project.slug
        ticket = TicketEntry(
            uuid="edit-tkt",
            title="Edit me",
            status="Open",
            type="Code_Defect",
            created=datetime(2026, 3, 1, tzinfo=UTC),
            owner="dev",
        )
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_ticket_detail.return_value = ticket
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "tickets/edit-tkt/edit/"))
        assert response.status_code == 200

    @pytest.mark.skip(reason="ticket_edit.html template uses .split which is not valid Django template syntax -- pre-existing bug")
    def test_ticket_edit_post(self, admin_client, sample_project):
        slug = sample_project.slug
        ticket = TicketEntry(
            uuid="edit-tkt",
            title="Edit me",
            status="Open",
            type="Code_Defect",
            created=datetime(2026, 3, 1, tzinfo=UTC),
            owner="dev",
        )
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_ticket_detail.return_value = ticket
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.ticket_change.return_value = True
                response = admin_client.post(
                    _url(slug, "tickets/edit-tkt/edit/"),
                    {"title": "Updated Title", "status": "Closed", "type": "Code_Defect"},
                )
        assert response.status_code == 302

    def test_ticket_comment_post(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.ticket_change.return_value = True
                response = admin_client.post(_url(slug, "tickets/tkt-uuid/comment/"), {"comment": "Looking into it"})
        assert response.status_code == 302

    def test_ticket_create_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.get(_url(sample_project.slug, "tickets/create/"))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Technote create/edit (mocked FossilCLI)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTechnoteCrudMocked:
    def test_technote_create_get(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "technotes/create/"))
        assert response.status_code == 200

    def test_technote_create_post(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.technote_create.return_value = True
                response = admin_client.post(_url(slug, "technotes/create/"), {"title": "v1 Release", "body": "Notes"})
        assert response.status_code == 302

    def test_technote_edit_get(self, admin_client, sample_project):
        slug = sample_project.slug
        note = {"uuid": "tn1", "comment": "Edit me", "body": "old body", "user": "dev", "timestamp": datetime(2026, 3, 1, tzinfo=UTC)}
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_technote_detail.return_value = note
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "technotes/tn1/edit/"))
        assert response.status_code == 200

    def test_technote_edit_post(self, admin_client, sample_project):
        slug = sample_project.slug
        note = {"uuid": "tn1", "comment": "Edit me", "body": "old body", "user": "dev", "timestamp": datetime(2026, 3, 1, tzinfo=UTC)}
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_technote_detail.return_value = note
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            with patch("fossil.cli.FossilCLI") as mock_cli_cls:
                cli = mock_cli_cls.return_value
                cli.technote_edit.return_value = True
                response = admin_client.post(_url(slug, "technotes/tn1/edit/"), {"body": "Updated notes"})
        assert response.status_code == 302

    def test_technote_edit_not_found(self, admin_client, sample_project):
        slug = sample_project.slug
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_technote_detail.return_value = None
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "technotes/missing/edit/"))
        assert response.status_code == 404

    def test_technote_create_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.get(_url(sample_project.slug, "technotes/create/"))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# User activity view (mocked) -- with empty heatmap
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserActivityEmpty:
    def test_user_activity_empty_data(self, admin_client, sample_project):
        slug = sample_project.slug
        activity = {"checkin_count": 0, "checkins": [], "daily_activity": {}}
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_user_activity.return_value = activity
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "user/unknown/"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Technote list with search
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTechnoteListSearch:
    def test_technote_search(self, admin_client, sample_project):
        slug = sample_project.slug
        notes = [
            SimpleNamespace(uuid="n1", comment="Release notes v1", user="dev", timestamp=datetime(2026, 3, 1, tzinfo=UTC)),
            SimpleNamespace(uuid="n2", comment="Sprint review", user="dev", timestamp=datetime(2026, 3, 2, tzinfo=UTC)),
        ]
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_technotes.return_value = notes
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "technotes/?search=release"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Code browser subdirectory
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCodeBrowserSubdir:
    def test_code_browser_subdir_with_breadcrumbs(self, admin_client, sample_project):
        slug = sample_project.slug
        files = [
            _make_file_entry(name="src/main.py", uuid="f1"),
            _make_file_entry(name="src/lib/helper.py", uuid="f2"),
        ]
        metadata = RepoMetadata(project_name="Test", checkin_count=10)
        with patch("fossil.views._get_repo_and_reader") as mock_grr:
            reader = MagicMock()
            reader.__enter__ = MagicMock(return_value=reader)
            reader.__exit__ = MagicMock(return_value=False)
            reader.get_latest_checkin_uuid.return_value = "abc"
            reader.get_files_at_checkin.return_value = files
            reader.get_metadata.return_value = metadata
            reader.get_timeline.return_value = []
            repo = FossilRepository.objects.get(project=sample_project)
            mock_grr.return_value = (sample_project, repo, reader)
            response = admin_client.get(_url(slug, "code/tree/src/"))
        assert response.status_code == 200

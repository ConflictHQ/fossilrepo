"""Tests for MCP server tool definitions and handlers.

Covers:
- Tool registry: all 17 tools registered with correct schemas
- Tool dispatch: execute_tool routes to correct handler
- Read handlers: list_projects, get_project, browse_code, read_file,
  get_timeline, get_checkin, search_code, list_tickets, get_ticket,
  list_wiki_pages, get_wiki_page, list_branches, get_file_blame,
  get_file_history, sql_query
- Write handlers: create_ticket, update_ticket
- Error handling: unknown tool, missing project, exceptions
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fossil.models import FossilRepository
from fossil.reader import (
    CheckinDetail,
    FileEntry,
    RepoMetadata,
    TicketEntry,
    TimelineEntry,
    WikiPage,
)
from mcp_server.tools import TOOLS, execute_tool

# Patch targets -- tools.py does deferred imports inside handler functions,
# so we patch at the source module rather than at the consumer.
_READER = "fossil.reader.FossilReader"
_CLI = "fossil.cli.FossilCLI"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


def _mock_reader():
    """Return a context-manager mock for FossilReader."""
    reader = MagicMock()
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=False)
    return reader


# ---------------------------------------------------------------------------
# Tool registry tests
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_all_17_tools_registered(self):
        assert len(TOOLS) == 17

    def test_tool_names_are_unique(self):
        names = [t.name for t in TOOLS]
        assert len(names) == len(set(names))

    def test_every_tool_has_input_schema(self):
        for tool in TOOLS:
            assert tool.inputSchema is not None
            assert tool.inputSchema.get("type") == "object"

    def test_every_tool_has_description(self):
        for tool in TOOLS:
            assert tool.description
            assert len(tool.description) > 10

    def test_expected_tools_present(self):
        names = {t.name for t in TOOLS}
        expected = {
            "list_projects",
            "get_project",
            "browse_code",
            "read_file",
            "get_timeline",
            "get_checkin",
            "search_code",
            "list_tickets",
            "get_ticket",
            "create_ticket",
            "update_ticket",
            "list_wiki_pages",
            "get_wiki_page",
            "list_branches",
            "get_file_blame",
            "get_file_history",
            "sql_query",
        }
        assert names == expected

    def test_slug_required_for_project_scoped_tools(self):
        """All tools except list_projects require a slug parameter."""
        for tool in TOOLS:
            if tool.name == "list_projects":
                assert "slug" not in tool.inputSchema.get("required", [])
            else:
                assert "slug" in tool.inputSchema.get("required", []), f"{tool.name} should require slug"


# ---------------------------------------------------------------------------
# Dispatch tests
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_unknown_tool_returns_error(self):
        result = execute_tool("nonexistent_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.django_db
    def test_missing_project_returns_error(self):
        result = execute_tool("get_project", {"slug": "does-not-exist"})
        assert "error" in result

    @pytest.mark.django_db
    def test_exception_in_handler_returns_error(self, sample_project):
        with patch("mcp_server.tools._get_repo", side_effect=RuntimeError("boom")):
            result = execute_tool("get_project", {"slug": sample_project.slug})
            assert "error" in result
            assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListProjects:
    def test_returns_all_active_projects(self, sample_project):
        result = execute_tool("list_projects", {})
        assert "projects" in result
        slugs = [p["slug"] for p in result["projects"]]
        assert sample_project.slug in slugs

    def test_excludes_deleted_projects(self, sample_project, admin_user):
        sample_project.soft_delete(user=admin_user)
        result = execute_tool("list_projects", {})
        slugs = [p["slug"] for p in result["projects"]]
        assert sample_project.slug not in slugs


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetProject:
    @patch(_READER)
    def test_returns_project_details(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_metadata.return_value = RepoMetadata(
            project_name="Test",
            checkin_count=10,
            ticket_count=3,
            wiki_page_count=2,
        )
        mock_reader_cls.return_value = reader

        with patch.object(type(fossil_repo_obj), "exists_on_disk", new_callable=lambda: property(lambda s: True)):
            result = execute_tool("get_project", {"slug": sample_project.slug})

        assert result["name"] == sample_project.name
        assert result["slug"] == sample_project.slug
        assert result["visibility"] == sample_project.visibility

    def test_nonexistent_slug_returns_error(self):
        result = execute_tool("get_project", {"slug": "no-such-project"})
        assert "error" in result


# ---------------------------------------------------------------------------
# browse_code
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBrowseCode:
    @patch(_READER)
    def test_lists_files_at_root(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_latest_checkin_uuid.return_value = "abc123"
        reader.get_files_at_checkin.return_value = [
            FileEntry(name="README.md", uuid="f1", size=100),
            FileEntry(name="src/main.py", uuid="f2", size=200),
        ]
        mock_reader_cls.return_value = reader

        result = execute_tool("browse_code", {"slug": sample_project.slug})
        assert len(result["files"]) == 2
        assert result["checkin"] == "abc123"

    @patch(_READER)
    def test_filters_by_path(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_latest_checkin_uuid.return_value = "abc123"
        reader.get_files_at_checkin.return_value = [
            FileEntry(name="README.md", uuid="f1", size=100),
            FileEntry(name="src/main.py", uuid="f2", size=200),
            FileEntry(name="src/utils.py", uuid="f3", size=150),
        ]
        mock_reader_cls.return_value = reader

        result = execute_tool("browse_code", {"slug": sample_project.slug, "path": "src"})
        assert len(result["files"]) == 2
        assert all(f["name"].startswith("src/") for f in result["files"])

    @patch(_READER)
    def test_empty_repo_returns_error(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_latest_checkin_uuid.return_value = None
        mock_reader_cls.return_value = reader

        result = execute_tool("browse_code", {"slug": sample_project.slug})
        assert "error" in result


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReadFile:
    @patch(_READER)
    def test_reads_text_file(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_latest_checkin_uuid.return_value = "abc123"
        reader.get_files_at_checkin.return_value = [
            FileEntry(name="README.md", uuid="f1", size=100),
        ]
        reader.get_file_content.return_value = b"# Hello World"
        mock_reader_cls.return_value = reader

        result = execute_tool("read_file", {"slug": sample_project.slug, "filepath": "README.md"})
        assert result["filepath"] == "README.md"
        assert result["content"] == "# Hello World"

    @patch(_READER)
    def test_binary_file_returns_metadata(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_latest_checkin_uuid.return_value = "abc123"
        reader.get_files_at_checkin.return_value = [
            FileEntry(name="image.png", uuid="f1", size=5000),
        ]
        reader.get_file_content.return_value = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00"
        mock_reader_cls.return_value = reader

        result = execute_tool("read_file", {"slug": sample_project.slug, "filepath": "image.png"})
        assert result["binary"] is True
        assert result["size"] > 0

    @patch(_READER)
    def test_file_not_found(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_latest_checkin_uuid.return_value = "abc123"
        reader.get_files_at_checkin.return_value = []
        mock_reader_cls.return_value = reader

        result = execute_tool("read_file", {"slug": sample_project.slug, "filepath": "nope.txt"})
        assert "error" in result
        assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# get_timeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetTimeline:
    @patch(_READER)
    def test_returns_checkins(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_timeline.return_value = [
            TimelineEntry(
                rid=1,
                uuid="abc123",
                event_type="ci",
                timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
                user="alice",
                comment="Initial commit",
                branch="trunk",
            ),
        ]
        mock_reader_cls.return_value = reader

        result = execute_tool("get_timeline", {"slug": sample_project.slug})
        assert len(result["checkins"]) == 1
        assert result["checkins"][0]["uuid"] == "abc123"
        assert result["checkins"][0]["user"] == "alice"

    @patch(_READER)
    def test_branch_filter(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_timeline.return_value = [
            TimelineEntry(
                rid=1,
                uuid="a1",
                event_type="ci",
                timestamp=datetime(2025, 1, 15, tzinfo=UTC),
                user="alice",
                comment="on trunk",
                branch="trunk",
            ),
            TimelineEntry(
                rid=2,
                uuid="b2",
                event_type="ci",
                timestamp=datetime(2025, 1, 14, tzinfo=UTC),
                user="bob",
                comment="on feature",
                branch="feature-x",
            ),
        ]
        mock_reader_cls.return_value = reader

        result = execute_tool("get_timeline", {"slug": sample_project.slug, "branch": "trunk"})
        assert len(result["checkins"]) == 1
        assert result["checkins"][0]["branch"] == "trunk"


# ---------------------------------------------------------------------------
# get_checkin
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetCheckin:
    @patch(_READER)
    def test_returns_checkin_detail(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_checkin_detail.return_value = CheckinDetail(
            uuid="abc123full",
            timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            user="alice",
            comment="Initial commit",
            branch="trunk",
            parent_uuid="parent000",
            files_changed=[{"name": "README.md", "change_type": "added", "uuid": "f1", "prev_uuid": ""}],
        )
        mock_reader_cls.return_value = reader

        result = execute_tool("get_checkin", {"slug": sample_project.slug, "uuid": "abc123"})
        assert result["uuid"] == "abc123full"
        assert len(result["files_changed"]) == 1

    @patch(_READER)
    def test_checkin_not_found(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_checkin_detail.return_value = None
        mock_reader_cls.return_value = reader

        result = execute_tool("get_checkin", {"slug": sample_project.slug, "uuid": "nonexistent"})
        assert "error" in result


# ---------------------------------------------------------------------------
# search_code
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSearchCode:
    @patch(_READER)
    def test_returns_search_results(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.search.return_value = {
            "checkins": [{"uuid": "abc", "timestamp": datetime(2025, 1, 15, tzinfo=UTC), "user": "alice", "comment": "fix bug"}],
            "tickets": [{"uuid": "tkt1", "title": "Bug report", "status": "Open", "created": datetime(2025, 1, 10, tzinfo=UTC)}],
            "wiki": [{"name": "Debugging"}],
        }
        mock_reader_cls.return_value = reader

        result = execute_tool("search_code", {"slug": sample_project.slug, "query": "bug"})
        assert len(result["checkins"]) == 1
        assert len(result["tickets"]) == 1
        assert len(result["wiki"]) == 1
        # Timestamps should be serialized to strings
        assert isinstance(result["checkins"][0]["timestamp"], str)


# ---------------------------------------------------------------------------
# list_tickets / get_ticket
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTickets:
    @patch(_READER)
    def test_list_tickets(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_tickets.return_value = [
            TicketEntry(
                uuid="tkt-001",
                title="Fix bug",
                status="Open",
                type="Code_Defect",
                created=datetime(2025, 1, 10, tzinfo=UTC),
                owner="alice",
                priority="High",
            ),
        ]
        mock_reader_cls.return_value = reader

        result = execute_tool("list_tickets", {"slug": sample_project.slug})
        assert len(result["tickets"]) == 1
        assert result["tickets"][0]["uuid"] == "tkt-001"

    @patch(_READER)
    def test_get_ticket_detail(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_ticket_detail.return_value = TicketEntry(
            uuid="tkt-001",
            title="Fix bug",
            status="Open",
            type="Code_Defect",
            created=datetime(2025, 1, 10, tzinfo=UTC),
            owner="alice",
            body="Detailed description",
            priority="High",
            severity="Critical",
        )
        reader.get_ticket_comments.return_value = [
            {"timestamp": datetime(2025, 1, 11, tzinfo=UTC), "user": "bob", "comment": "Reproduced", "mimetype": "text/plain"},
        ]
        mock_reader_cls.return_value = reader

        result = execute_tool("get_ticket", {"slug": sample_project.slug, "uuid": "tkt-001"})
        assert result["title"] == "Fix bug"
        assert result["body"] == "Detailed description"
        assert len(result["comments"]) == 1

    @patch(_READER)
    def test_ticket_not_found(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_ticket_detail.return_value = None
        mock_reader_cls.return_value = reader

        result = execute_tool("get_ticket", {"slug": sample_project.slug, "uuid": "nonexistent"})
        assert "error" in result


# ---------------------------------------------------------------------------
# create_ticket / update_ticket
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWriteTickets:
    @patch(_CLI)
    def test_create_ticket(self, mock_cli_cls, sample_project, fossil_repo_obj):
        cli = MagicMock()
        cli.ticket_add.return_value = True
        mock_cli_cls.return_value = cli

        result = execute_tool(
            "create_ticket",
            {
                "slug": sample_project.slug,
                "title": "New bug",
                "body": "Something is broken",
            },
        )
        assert result["success"] is True
        assert result["title"] == "New bug"
        cli.ticket_add.assert_called_once()
        call_args = cli.ticket_add.call_args
        fields = call_args[0][1]
        assert fields["title"] == "New bug"
        assert fields["comment"] == "Something is broken"
        assert fields["status"] == "Open"

    @patch(_CLI)
    def test_create_ticket_failure(self, mock_cli_cls, sample_project, fossil_repo_obj):
        cli = MagicMock()
        cli.ticket_add.return_value = False
        mock_cli_cls.return_value = cli

        result = execute_tool(
            "create_ticket",
            {
                "slug": sample_project.slug,
                "title": "Failing",
                "body": "Will fail",
            },
        )
        assert "error" in result

    @patch(_CLI)
    def test_update_ticket_status(self, mock_cli_cls, sample_project, fossil_repo_obj):
        cli = MagicMock()
        cli.ticket_change.return_value = True
        mock_cli_cls.return_value = cli

        result = execute_tool(
            "update_ticket",
            {
                "slug": sample_project.slug,
                "uuid": "tkt-001",
                "status": "Closed",
            },
        )
        assert result["success"] is True
        call_args = cli.ticket_change.call_args
        assert call_args[0][1] == "tkt-001"
        assert call_args[0][2]["status"] == "Closed"

    @patch(_CLI)
    def test_update_ticket_comment(self, mock_cli_cls, sample_project, fossil_repo_obj):
        cli = MagicMock()
        cli.ticket_change.return_value = True
        mock_cli_cls.return_value = cli

        result = execute_tool(
            "update_ticket",
            {
                "slug": sample_project.slug,
                "uuid": "tkt-001",
                "comment": "Fixed in latest push",
            },
        )
        assert result["success"] is True
        call_args = cli.ticket_change.call_args
        assert call_args[0][2]["icomment"] == "Fixed in latest push"

    @patch(_CLI)
    def test_update_ticket_no_fields(self, mock_cli_cls, sample_project, fossil_repo_obj):
        cli = MagicMock()
        mock_cli_cls.return_value = cli

        result = execute_tool(
            "update_ticket",
            {
                "slug": sample_project.slug,
                "uuid": "tkt-001",
            },
        )
        assert "error" in result
        assert "No fields" in result["error"]


# ---------------------------------------------------------------------------
# wiki handlers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWiki:
    @patch(_READER)
    def test_list_wiki_pages(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_wiki_pages.return_value = [
            WikiPage(name="Home", content="", last_modified=datetime(2025, 1, 12, tzinfo=UTC), user="alice"),
            WikiPage(name="FAQ", content="", last_modified=datetime(2025, 1, 13, tzinfo=UTC), user="bob"),
        ]
        mock_reader_cls.return_value = reader

        result = execute_tool("list_wiki_pages", {"slug": sample_project.slug})
        assert len(result["pages"]) == 2
        assert result["pages"][0]["name"] == "Home"

    @patch(_READER)
    def test_get_wiki_page(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_wiki_page.return_value = WikiPage(
            name="Home",
            content="# Welcome\nThis is home.",
            last_modified=datetime(2025, 1, 12, tzinfo=UTC),
            user="alice",
        )
        mock_reader_cls.return_value = reader

        result = execute_tool("get_wiki_page", {"slug": sample_project.slug, "page_name": "Home"})
        assert result["name"] == "Home"
        assert "Welcome" in result["content"]

    @patch(_READER)
    def test_wiki_page_not_found(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_wiki_page.return_value = None
        mock_reader_cls.return_value = reader

        result = execute_tool("get_wiki_page", {"slug": sample_project.slug, "page_name": "Missing"})
        assert "error" in result


# ---------------------------------------------------------------------------
# branches, blame, file history
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBranchesAndHistory:
    @patch(_READER)
    def test_list_branches(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_branches.return_value = [
            {
                "name": "trunk",
                "last_checkin": datetime(2025, 1, 15, tzinfo=UTC),
                "last_user": "alice",
                "checkin_count": 30,
                "last_uuid": "abc123",
            },
        ]
        mock_reader_cls.return_value = reader

        result = execute_tool("list_branches", {"slug": sample_project.slug})
        assert len(result["branches"]) == 1
        assert result["branches"][0]["name"] == "trunk"

    @patch(_CLI)
    def test_get_file_blame(self, mock_cli_cls, sample_project, fossil_repo_obj):
        cli = MagicMock()
        cli.blame.return_value = [
            {"uuid": "aaa", "date": "2025-01-15", "user": "alice", "text": "line 1"},
            {"uuid": "bbb", "date": "2025-01-14", "user": "bob", "text": "line 2"},
        ]
        mock_cli_cls.return_value = cli

        result = execute_tool("get_file_blame", {"slug": sample_project.slug, "filepath": "main.py"})
        assert result["filepath"] == "main.py"
        assert len(result["lines"]) == 2
        assert result["total"] == 2

    @patch(_READER)
    def test_get_file_history(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        reader.get_file_history.return_value = [
            {"uuid": "c1", "timestamp": datetime(2025, 1, 15, tzinfo=UTC), "user": "alice", "comment": "Update"},
            {"uuid": "c2", "timestamp": datetime(2025, 1, 14, tzinfo=UTC), "user": "bob", "comment": "Create"},
        ]
        mock_reader_cls.return_value = reader

        result = execute_tool("get_file_history", {"slug": sample_project.slug, "filepath": "main.py"})
        assert result["filepath"] == "main.py"
        assert len(result["history"]) == 2
        # Timestamps should be serialized
        assert isinstance(result["history"][0]["timestamp"], str)


# ---------------------------------------------------------------------------
# sql_query
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSqlQuery:
    def test_rejects_non_select(self, sample_project, fossil_repo_obj):
        result = execute_tool("sql_query", {"slug": sample_project.slug, "sql": "DELETE FROM ticket"})
        assert "error" in result
        assert "SELECT" in result["error"]

    def test_rejects_empty_query(self, sample_project, fossil_repo_obj):
        result = execute_tool("sql_query", {"slug": sample_project.slug, "sql": ""})
        assert "error" in result

    def test_rejects_drop(self, sample_project, fossil_repo_obj):
        result = execute_tool("sql_query", {"slug": sample_project.slug, "sql": "SELECT 1; DROP TABLE ticket"})
        assert "error" in result

    @patch(_READER)
    def test_valid_select(self, mock_reader_cls, sample_project, fossil_repo_obj):
        reader = _mock_reader()
        mock_cursor = MagicMock()
        mock_cursor.description = [("tkt_uuid",), ("title",)]
        mock_cursor.fetchmany.return_value = [("uuid-1", "Bug one"), ("uuid-2", "Bug two")]
        reader.conn.cursor.return_value = mock_cursor
        mock_reader_cls.return_value = reader

        result = execute_tool("sql_query", {"slug": sample_project.slug, "sql": "SELECT tkt_uuid, title FROM ticket"})
        assert result["columns"] == ["tkt_uuid", "title"]
        assert len(result["rows"]) == 2
        assert result["count"] == 2


# ---------------------------------------------------------------------------
# Server module smoke test
# ---------------------------------------------------------------------------


class TestServerModule:
    def test_server_instance_exists(self):
        from mcp_server.server import server

        assert server.name == "fossilrepo"

    def test_main_is_coroutine(self):
        import inspect

        from mcp_server.server import main

        assert inspect.iscoroutinefunction(main)

    def test_entry_point_function_exists(self):
        from mcp_server.__main__ import run

        assert callable(run)

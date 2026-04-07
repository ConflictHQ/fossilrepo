import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.models import FossilRepository
from fossil.ticket_reports import TicketReport
from organization.models import Team
from projects.models import ProjectTeam


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def public_report(fossil_repo_obj, admin_user):
    return TicketReport.objects.create(
        repository=fossil_repo_obj,
        title="Open Tickets",
        description="All open tickets",
        sql_query="SELECT tkt_uuid, title, status FROM ticket WHERE status = 'Open'",
        is_public=True,
        created_by=admin_user,
    )


@pytest.fixture
def private_report(fossil_repo_obj, admin_user):
    return TicketReport.objects.create(
        repository=fossil_repo_obj,
        title="Internal Metrics",
        description="Admin-only report",
        sql_query="SELECT COUNT(*) as total FROM ticket",
        is_public=False,
        created_by=admin_user,
    )


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """User with write access but not admin."""
    writer = User.objects.create_user(username="writer", password="testpass123")
    team = Team.objects.create(name="Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="writer", password="testpass123")
    return client


@pytest.fixture
def reader_user(db, admin_user, sample_project):
    """User with read access only."""
    reader = User.objects.create_user(username="reader", password="testpass123")
    team = Team.objects.create(name="Readers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(reader)
    ProjectTeam.objects.create(project=sample_project, team=team, role="read", created_by=admin_user)
    return reader


@pytest.fixture
def reader_client(reader_user):
    client = Client()
    client.login(username="reader", password="testpass123")
    return client


# --- SQL Validation Tests ---


@pytest.mark.django_db
class TestTicketReportSQLValidation:
    def test_valid_select(self):
        assert TicketReport.validate_sql("SELECT * FROM ticket") is None

    def test_valid_select_with_where(self):
        assert TicketReport.validate_sql("SELECT title, status FROM ticket WHERE status = 'Open'") is None

    def test_valid_select_with_join(self):
        assert TicketReport.validate_sql("SELECT t.title, tc.icomment FROM ticket t JOIN ticketchng tc ON t.tkt_id = tc.tkt_id") is None

    def test_reject_empty(self):
        assert TicketReport.validate_sql("") is not None
        assert "empty" in TicketReport.validate_sql("").lower()

    def test_reject_insert(self):
        error = TicketReport.validate_sql("INSERT INTO ticket (title) VALUES ('hack')")
        assert error is not None
        assert "select" in error.lower() or "forbidden" in error.lower()

    def test_reject_update(self):
        error = TicketReport.validate_sql("UPDATE ticket SET title = 'hacked'")
        assert error is not None

    def test_reject_delete(self):
        error = TicketReport.validate_sql("DELETE FROM ticket")
        assert error is not None

    def test_reject_drop(self):
        error = TicketReport.validate_sql("DROP TABLE ticket")
        assert error is not None

    def test_reject_alter(self):
        error = TicketReport.validate_sql("ALTER TABLE ticket ADD COLUMN evil TEXT")
        assert error is not None

    def test_reject_create(self):
        error = TicketReport.validate_sql("CREATE TABLE evil (id INTEGER)")
        assert error is not None

    def test_reject_attach(self):
        error = TicketReport.validate_sql("ATTACH DATABASE ':memory:' AS evil")
        assert error is not None

    def test_reject_pragma(self):
        error = TicketReport.validate_sql("PRAGMA table_info(ticket)")
        assert error is not None

    def test_reject_multiple_statements(self):
        error = TicketReport.validate_sql("SELECT 1; DROP TABLE ticket")
        assert error is not None
        # May be caught by forbidden keyword or multiple statement check
        assert "multiple" in error.lower() or "forbidden" in error.lower()

    def test_reject_multiple_statements_pure(self):
        """Semicolons without forbidden keywords should also be rejected."""
        error = TicketReport.validate_sql("SELECT 1; SELECT 2")
        assert error is not None
        assert "multiple" in error.lower()

    def test_reject_non_select_start(self):
        error = TicketReport.validate_sql("WITH cte AS (DELETE FROM ticket) SELECT * FROM cte")
        assert error is not None
        assert "SELECT" in error


# --- Model Tests ---


@pytest.mark.django_db
class TestTicketReportModel:
    def test_create_report(self, public_report):
        assert public_report.pk is not None
        assert str(public_report) == "Open Tickets"

    def test_soft_delete(self, public_report, admin_user):
        public_report.soft_delete(user=admin_user)
        assert public_report.is_deleted
        assert TicketReport.objects.filter(pk=public_report.pk).count() == 0
        assert TicketReport.all_objects.filter(pk=public_report.pk).count() == 1

    def test_ordering(self, fossil_repo_obj, admin_user):
        r_b = TicketReport.objects.create(repository=fossil_repo_obj, title="B Report", sql_query="SELECT 1", created_by=admin_user)
        r_a = TicketReport.objects.create(repository=fossil_repo_obj, title="A Report", sql_query="SELECT 1", created_by=admin_user)
        reports = list(TicketReport.objects.filter(repository=fossil_repo_obj))
        assert reports[0] == r_a  # alphabetical
        assert reports[1] == r_b


# --- List View Tests ---


@pytest.mark.django_db
class TestTicketReportsListView:
    def test_list_reports_admin(self, admin_client, sample_project, public_report, private_report):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Open Tickets" in content
        assert "Internal Metrics" in content  # admin sees private reports

    def test_list_reports_reader_hides_private(self, reader_client, sample_project, public_report, private_report):
        response = reader_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Open Tickets" in content
        assert "Internal Metrics" not in content  # reader cannot see private reports

    def test_list_empty(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/")
        assert response.status_code == 200
        assert "No ticket reports defined" in response.content.decode()

    def test_list_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/")
        # Private project: anonymous user gets 403 from require_project_read
        assert response.status_code == 403


# --- Create View Tests ---


@pytest.mark.django_db
class TestTicketReportCreateView:
    def test_get_form(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/create/")
        assert response.status_code == 200
        assert "Create Ticket Report" in response.content.decode()

    def test_create_report(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/reports/create/",
            {
                "title": "Critical Bugs",
                "description": "All critical severity tickets",
                "sql_query": "SELECT tkt_uuid, title FROM ticket WHERE severity = 'Critical'",
                "is_public": "on",
            },
        )
        assert response.status_code == 302
        report = TicketReport.objects.get(title="Critical Bugs")
        assert report.is_public is True
        assert "Critical" in report.sql_query

    def test_create_report_rejects_dangerous_sql(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/reports/create/",
            {
                "title": "Evil Report",
                "sql_query": "DROP TABLE ticket",
            },
        )
        assert response.status_code == 200  # re-renders form with error
        assert TicketReport.objects.filter(title="Evil Report").count() == 0

    def test_create_denied_for_writer(self, writer_client, sample_project):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/reports/create/",
            {"title": "Hack", "sql_query": "SELECT 1"},
        )
        assert response.status_code == 403

    def test_create_denied_for_reader(self, reader_client, sample_project):
        response = reader_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/reports/create/",
            {"title": "Hack", "sql_query": "SELECT 1"},
        )
        assert response.status_code == 403


# --- Edit View Tests ---


@pytest.mark.django_db
class TestTicketReportEditView:
    def test_get_edit_form(self, admin_client, sample_project, public_report):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/{public_report.pk}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Open Tickets" in content

    def test_edit_report(self, admin_client, sample_project, public_report):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/reports/{public_report.pk}/edit/",
            {
                "title": "Open Tickets (Updated)",
                "description": "Updated description",
                "sql_query": "SELECT tkt_uuid, title, status FROM ticket WHERE status = 'Open' ORDER BY tkt_ctime DESC",
                "is_public": "on",
            },
        )
        assert response.status_code == 302
        public_report.refresh_from_db()
        assert public_report.title == "Open Tickets (Updated)"

    def test_edit_rejects_dangerous_sql(self, admin_client, sample_project, public_report):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/reports/{public_report.pk}/edit/",
            {
                "title": "Open Tickets",
                "sql_query": "DELETE FROM ticket",
            },
        )
        assert response.status_code == 200  # re-renders form
        public_report.refresh_from_db()
        assert "DELETE" not in public_report.sql_query

    def test_edit_denied_for_writer(self, writer_client, sample_project, public_report):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/reports/{public_report.pk}/edit/",
            {"title": "Hacked", "sql_query": "SELECT 1"},
        )
        assert response.status_code == 403

    def test_edit_nonexistent(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/99999/edit/")
        assert response.status_code == 404


# --- Run View Tests ---


@pytest.mark.django_db
class TestTicketReportRunView:
    def test_run_public_report_as_reader(self, reader_client, sample_project, public_report):
        """Readers can run public reports, but report may error if .fossil file not on disk."""
        response = reader_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/{public_report.pk}/")
        # The .fossil file does not exist on disk in test env, so we get a database error
        # but the view itself should not raise a 403/404 — it renders the error in-page
        assert response.status_code == 200

    def test_run_private_report_denied_for_reader(self, reader_client, sample_project, private_report):
        response = reader_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/{private_report.pk}/")
        assert response.status_code == 403

    def test_run_private_report_allowed_for_admin(self, admin_client, sample_project, private_report):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/{private_report.pk}/")
        assert response.status_code == 200

    def test_run_nonexistent_report(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/99999/")
        assert response.status_code == 404

    def test_run_denied_for_anon(self, client, sample_project, public_report):
        response = client.get(f"/projects/{sample_project.slug}/fossil/tickets/reports/{public_report.pk}/")
        # Private project: anonymous user gets 403 from require_project_read
        assert response.status_code == 403

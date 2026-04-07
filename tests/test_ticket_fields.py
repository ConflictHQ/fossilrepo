import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.models import FossilRepository
from fossil.ticket_fields import TicketFieldDefinition
from organization.models import Team
from projects.models import ProjectTeam


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def text_field(fossil_repo_obj, admin_user):
    return TicketFieldDefinition.objects.create(
        repository=fossil_repo_obj,
        name="component",
        label="Component",
        field_type="text",
        is_required=False,
        sort_order=1,
        created_by=admin_user,
    )


@pytest.fixture
def select_field(fossil_repo_obj, admin_user):
    return TicketFieldDefinition.objects.create(
        repository=fossil_repo_obj,
        name="platform",
        label="Platform",
        field_type="select",
        choices="Linux\nWindows\nmacOS",
        is_required=True,
        sort_order=2,
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


# --- Model Tests ---


@pytest.mark.django_db
class TestTicketFieldDefinitionModel:
    def test_create_field(self, text_field):
        assert text_field.pk is not None
        assert str(text_field) == "Component (component)"

    def test_choices_list(self, select_field):
        assert select_field.choices_list == ["Linux", "Windows", "macOS"]

    def test_choices_list_empty(self, text_field):
        assert text_field.choices_list == []

    def test_soft_delete(self, text_field, admin_user):
        text_field.soft_delete(user=admin_user)
        assert text_field.is_deleted
        assert TicketFieldDefinition.objects.filter(pk=text_field.pk).count() == 0
        assert TicketFieldDefinition.all_objects.filter(pk=text_field.pk).count() == 1

    def test_ordering(self, text_field, select_field):
        fields = list(TicketFieldDefinition.objects.filter(repository=text_field.repository))
        assert fields[0] == text_field  # sort_order=1
        assert fields[1] == select_field  # sort_order=2

    def test_unique_name_per_repo(self, fossil_repo_obj, admin_user, text_field):
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            TicketFieldDefinition.objects.create(
                repository=fossil_repo_obj,
                name="component",
                label="Duplicate Component",
                created_by=admin_user,
            )


# --- List View Tests ---


@pytest.mark.django_db
class TestTicketFieldsListView:
    def test_list_fields(self, admin_client, sample_project, text_field, select_field):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/fields/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Component" in content
        assert "Platform" in content

    def test_list_empty(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/fields/")
        assert response.status_code == 200
        assert "No custom ticket fields defined" in response.content.decode()

    def test_list_denied_for_writer(self, writer_client, sample_project, text_field):
        """Custom field management requires admin."""
        response = writer_client.get(f"/projects/{sample_project.slug}/fossil/tickets/fields/")
        assert response.status_code == 403

    def test_list_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/tickets/fields/")
        assert response.status_code == 302  # redirect to login


# --- Create View Tests ---


@pytest.mark.django_db
class TestTicketFieldCreateView:
    def test_get_form(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/fields/create/")
        assert response.status_code == 200
        assert "Add Custom Ticket Field" in response.content.decode()

    def test_create_field(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/fields/create/",
            {
                "name": "affected_version",
                "label": "Affected Version",
                "field_type": "text",
                "sort_order": "5",
            },
        )
        assert response.status_code == 302
        field = TicketFieldDefinition.objects.get(name="affected_version")
        assert field.label == "Affected Version"
        assert field.field_type == "text"
        assert field.sort_order == 5
        assert field.is_required is False

    def test_create_select_field_with_choices(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/fields/create/",
            {
                "name": "env",
                "label": "Environment",
                "field_type": "select",
                "choices": "dev\nstaging\nprod",
                "is_required": "on",
                "sort_order": "0",
            },
        )
        assert response.status_code == 302
        field = TicketFieldDefinition.objects.get(name="env")
        assert field.choices_list == ["dev", "staging", "prod"]
        assert field.is_required is True

    def test_create_duplicate_name_rejected(self, admin_client, sample_project, text_field):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/fields/create/",
            {"name": "component", "label": "Another Component", "field_type": "text", "sort_order": "0"},
        )
        assert response.status_code == 200  # re-renders form
        assert TicketFieldDefinition.objects.filter(name="component").count() == 1

    def test_create_denied_for_writer(self, writer_client, sample_project):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/fields/create/",
            {"name": "evil", "label": "Evil", "field_type": "text", "sort_order": "0"},
        )
        assert response.status_code == 403


# --- Edit View Tests ---


@pytest.mark.django_db
class TestTicketFieldEditView:
    def test_get_edit_form(self, admin_client, sample_project, text_field):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/fields/{text_field.pk}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "component" in content
        assert "Component" in content

    def test_edit_field(self, admin_client, sample_project, text_field):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/fields/{text_field.pk}/edit/",
            {"name": "component", "label": "SW Component", "field_type": "text", "sort_order": "10"},
        )
        assert response.status_code == 302
        text_field.refresh_from_db()
        assert text_field.label == "SW Component"
        assert text_field.sort_order == 10

    def test_edit_denied_for_writer(self, writer_client, sample_project, text_field):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/tickets/fields/{text_field.pk}/edit/",
            {"name": "component", "label": "Hacked", "field_type": "text", "sort_order": "0"},
        )
        assert response.status_code == 403

    def test_edit_nonexistent(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/fields/99999/edit/")
        assert response.status_code == 404


# --- Delete View Tests ---


@pytest.mark.django_db
class TestTicketFieldDeleteView:
    def test_delete_field(self, admin_client, sample_project, text_field):
        response = admin_client.post(f"/projects/{sample_project.slug}/fossil/tickets/fields/{text_field.pk}/delete/")
        assert response.status_code == 302
        text_field.refresh_from_db()
        assert text_field.is_deleted

    def test_delete_get_redirects(self, admin_client, sample_project, text_field):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/tickets/fields/{text_field.pk}/delete/")
        assert response.status_code == 302

    def test_delete_denied_for_writer(self, writer_client, sample_project, text_field):
        response = writer_client.post(f"/projects/{sample_project.slug}/fossil/tickets/fields/{text_field.pk}/delete/")
        assert response.status_code == 403

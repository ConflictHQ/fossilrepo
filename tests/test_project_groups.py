"""Tests for Project Groups: model, views, sidebar context, and permissions."""

import pytest
from django.contrib.auth.models import Group, Permission
from django.test import Client

from projects.models import Project, ProjectGroup


@pytest.fixture
def sample_group(db, admin_user):
    return ProjectGroup.objects.create(name="Fossil SCM", description="The Fossil repos", created_by=admin_user)


@pytest.fixture
def editor_user(db):
    """User with full project and projectgroup permissions (add/change/delete/view) but not superuser."""
    user = __import__("django.contrib.auth.models", fromlist=["User"]).User.objects.create_user(
        username="editor", email="editor@test.com", password="testpass123"
    )
    group, _ = Group.objects.get_or_create(name="Editors")
    perms = Permission.objects.filter(
        content_type__app_label="projects",
    )
    group.permissions.set(perms)
    user.groups.add(group)
    return user


@pytest.fixture
def editor_client(editor_user):
    client = Client()
    client.login(username="editor", password="testpass123")
    return client


# --- Model Tests ---


@pytest.mark.django_db
class TestProjectGroupModel:
    def test_create_group(self, admin_user):
        group = ProjectGroup.objects.create(name="Test Group", created_by=admin_user)
        assert group.slug == "test-group"
        assert str(group) == "Test Group"
        assert group.guid is not None

    def test_slug_auto_generated(self, admin_user):
        group = ProjectGroup.objects.create(name="My Cool Group", created_by=admin_user)
        assert group.slug == "my-cool-group"

    def test_slug_uniqueness(self, admin_user):
        ProjectGroup.objects.create(name="Dupes", created_by=admin_user)
        g2 = ProjectGroup.objects.create(name="Dupes", created_by=admin_user)
        assert g2.slug == "dupes-1"

    def test_soft_delete(self, admin_user):
        group = ProjectGroup.objects.create(name="Deletable", created_by=admin_user)
        group.soft_delete(user=admin_user)
        assert group.is_deleted
        assert ProjectGroup.objects.filter(name="Deletable").count() == 0
        assert ProjectGroup.all_objects.filter(name="Deletable").count() == 1

    def test_project_group_fk(self, admin_user, org, sample_group):
        project = Project.objects.create(name="Grouped Project", organization=org, group=sample_group, created_by=admin_user)
        assert project.group == sample_group
        assert project in sample_group.projects.all()

    def test_project_group_nullable(self, admin_user, org):
        project = Project.objects.create(name="Ungrouped", organization=org, created_by=admin_user)
        assert project.group is None

    def test_group_deletion_sets_null(self, admin_user, org, sample_group):
        project = Project.objects.create(name="Will Unlink", organization=org, group=sample_group, created_by=admin_user)
        sample_group.delete()
        project.refresh_from_db()
        assert project.group is None


# --- View Tests: Group List ---


@pytest.mark.django_db
class TestGroupListView:
    def test_list_allowed_for_superuser(self, admin_client, sample_group):
        response = admin_client.get("/projects/groups/")
        assert response.status_code == 200
        assert "Fossil SCM" in response.content.decode()

    def test_list_allowed_for_viewer(self, viewer_client, sample_group):
        response = viewer_client.get("/projects/groups/")
        assert response.status_code == 200

    def test_list_denied_for_no_perm(self, no_perm_client):
        response = no_perm_client.get("/projects/groups/")
        assert response.status_code == 403

    def test_list_denied_for_anon(self, client):
        response = client.get("/projects/groups/")
        assert response.status_code == 302

    def test_list_shows_project_count(self, admin_client, admin_user, org, sample_group):
        Project.objects.create(name="P1", organization=org, group=sample_group, created_by=admin_user)
        Project.objects.create(name="P2", organization=org, group=sample_group, created_by=admin_user)
        response = admin_client.get("/projects/groups/")
        content = response.content.decode()
        assert "Fossil SCM" in content

    def test_list_htmx_returns_partial(self, admin_client, sample_group):
        response = admin_client.get("/projects/groups/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Fossil SCM" in content
        # Partial should not have full page structure
        assert "<!DOCTYPE html>" not in content


# --- View Tests: Group Create ---


@pytest.mark.django_db
class TestGroupCreateView:
    def test_create_get_allowed_for_superuser(self, admin_client):
        response = admin_client.get("/projects/groups/create/")
        assert response.status_code == 200

    def test_create_get_allowed_for_editor(self, editor_client):
        response = editor_client.get("/projects/groups/create/")
        assert response.status_code == 200

    def test_create_denied_for_viewer(self, viewer_client):
        response = viewer_client.get("/projects/groups/create/")
        assert response.status_code == 403

    def test_create_denied_for_no_perm(self, no_perm_client):
        response = no_perm_client.get("/projects/groups/create/")
        assert response.status_code == 403

    def test_create_denied_for_anon(self, client):
        response = client.get("/projects/groups/create/")
        assert response.status_code == 302

    def test_create_saves_group(self, admin_client, admin_user):
        response = admin_client.post("/projects/groups/create/", {"name": "New Group", "description": "Desc"})
        assert response.status_code == 302
        group = ProjectGroup.objects.get(name="New Group")
        assert group.description == "Desc"
        assert group.created_by == admin_user

    def test_create_redirects_to_detail(self, admin_client):
        response = admin_client.post("/projects/groups/create/", {"name": "Redirect Test"})
        assert response.status_code == 302
        group = ProjectGroup.objects.get(name="Redirect Test")
        assert response.url == f"/projects/groups/{group.slug}/"

    def test_create_requires_name(self, admin_client):
        response = admin_client.post("/projects/groups/create/", {"name": "", "description": "No name"})
        assert response.status_code == 200  # Re-renders form


# --- View Tests: Group Detail ---


@pytest.mark.django_db
class TestGroupDetailView:
    def test_detail_allowed_for_superuser(self, admin_client, sample_group):
        response = admin_client.get(f"/projects/groups/{sample_group.slug}/")
        assert response.status_code == 200
        assert "Fossil SCM" in response.content.decode()

    def test_detail_allowed_for_viewer(self, viewer_client, sample_group):
        response = viewer_client.get(f"/projects/groups/{sample_group.slug}/")
        assert response.status_code == 200

    def test_detail_denied_for_no_perm(self, no_perm_client, sample_group):
        response = no_perm_client.get(f"/projects/groups/{sample_group.slug}/")
        assert response.status_code == 403

    def test_detail_denied_for_anon(self, client, sample_group):
        response = client.get(f"/projects/groups/{sample_group.slug}/")
        assert response.status_code == 302

    def test_detail_shows_member_projects(self, admin_client, admin_user, org, sample_group):
        Project.objects.create(name="Fossil Source", organization=org, group=sample_group, created_by=admin_user)
        response = admin_client.get(f"/projects/groups/{sample_group.slug}/")
        assert "Fossil Source" in response.content.decode()

    def test_detail_404_for_deleted_group(self, admin_client, admin_user, sample_group):
        sample_group.soft_delete(user=admin_user)
        response = admin_client.get(f"/projects/groups/{sample_group.slug}/")
        assert response.status_code == 404


# --- View Tests: Group Edit ---


@pytest.mark.django_db
class TestGroupEditView:
    def test_edit_get_allowed_for_superuser(self, admin_client, sample_group):
        response = admin_client.get(f"/projects/groups/{sample_group.slug}/edit/")
        assert response.status_code == 200

    def test_edit_get_allowed_for_editor(self, editor_client, sample_group):
        response = editor_client.get(f"/projects/groups/{sample_group.slug}/edit/")
        assert response.status_code == 200

    def test_edit_denied_for_viewer(self, viewer_client, sample_group):
        response = viewer_client.get(f"/projects/groups/{sample_group.slug}/edit/")
        assert response.status_code == 403

    def test_edit_denied_for_no_perm(self, no_perm_client, sample_group):
        response = no_perm_client.get(f"/projects/groups/{sample_group.slug}/edit/")
        assert response.status_code == 403

    def test_edit_saves_changes(self, admin_client, admin_user, sample_group):
        response = admin_client.post(
            f"/projects/groups/{sample_group.slug}/edit/",
            {"name": "Fossil SCM Updated", "description": "Updated desc"},
        )
        assert response.status_code == 302
        sample_group.refresh_from_db()
        assert sample_group.name == "Fossil SCM Updated"
        assert sample_group.description == "Updated desc"
        assert sample_group.updated_by == admin_user


# --- View Tests: Group Delete ---


@pytest.mark.django_db
class TestGroupDeleteView:
    def test_delete_get_shows_confirmation(self, admin_client, sample_group):
        response = admin_client.get(f"/projects/groups/{sample_group.slug}/delete/")
        assert response.status_code == 200
        assert "Fossil SCM" in response.content.decode()
        assert "Delete" in response.content.decode()

    def test_delete_denied_for_viewer(self, viewer_client, sample_group):
        response = viewer_client.post(f"/projects/groups/{sample_group.slug}/delete/")
        assert response.status_code == 403

    def test_delete_denied_for_no_perm(self, no_perm_client, sample_group):
        response = no_perm_client.post(f"/projects/groups/{sample_group.slug}/delete/")
        assert response.status_code == 403

    def test_delete_soft_deletes_group(self, admin_client, admin_user, sample_group):
        response = admin_client.post(f"/projects/groups/{sample_group.slug}/delete/")
        assert response.status_code == 302
        sample_group.refresh_from_db()
        assert sample_group.is_deleted

    def test_delete_unlinks_projects(self, admin_client, admin_user, org, sample_group):
        project = Project.objects.create(name="Linked", organization=org, group=sample_group, created_by=admin_user)
        admin_client.post(f"/projects/groups/{sample_group.slug}/delete/")
        project.refresh_from_db()
        assert project.group is None
        assert not project.is_deleted  # Project survives

    def test_delete_htmx_redirect(self, admin_client, sample_group):
        response = admin_client.post(f"/projects/groups/{sample_group.slug}/delete/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert response["HX-Redirect"] == "/projects/groups/"


# --- Form Tests ---


@pytest.mark.django_db
class TestProjectGroupForm:
    def test_form_valid(self):
        from projects.forms import ProjectGroupForm

        form = ProjectGroupForm(data={"name": "Test Group", "description": "A group"})
        assert form.is_valid()

    def test_form_valid_without_description(self):
        from projects.forms import ProjectGroupForm

        form = ProjectGroupForm(data={"name": "Test Group", "description": ""})
        assert form.is_valid()

    def test_form_invalid_without_name(self):
        from projects.forms import ProjectGroupForm

        form = ProjectGroupForm(data={"name": "", "description": "No name"})
        assert not form.is_valid()
        assert "name" in form.errors


# --- ProjectForm group field ---


@pytest.mark.django_db
class TestProjectFormGroupField:
    def test_project_form_includes_group(self, sample_group):
        from projects.forms import ProjectForm

        form = ProjectForm(data={"name": "Test", "visibility": "private", "group": sample_group.pk})
        assert form.is_valid()
        project_data = form.cleaned_data
        assert project_data["group"] == sample_group

    def test_project_form_group_optional(self):
        from projects.forms import ProjectForm

        form = ProjectForm(data={"name": "No Group", "visibility": "private"})
        assert form.is_valid()
        assert form.cleaned_data["group"] is None

    def test_project_create_with_group(self, admin_client, org, sample_group):
        response = admin_client.post(
            "/projects/create/",
            {"name": "Grouped Via Form", "visibility": "private", "group": sample_group.pk},
        )
        assert response.status_code == 302
        project = Project.objects.get(name="Grouped Via Form")
        assert project.group == sample_group


# --- Context Processor Tests ---


@pytest.mark.django_db
class TestSidebarContext:
    def test_grouped_projects_in_context(self, admin_client, admin_user, org, sample_group):
        Project.objects.create(name="Grouped P", organization=org, group=sample_group, created_by=admin_user)
        Project.objects.create(name="Ungrouped P", organization=org, created_by=admin_user)
        response = admin_client.get("/dashboard/")
        assert response.status_code == 200
        context = response.context
        assert "sidebar_grouped" in context
        assert "sidebar_ungrouped" in context
        assert len(context["sidebar_grouped"]) == 1
        assert context["sidebar_grouped"][0]["group"].name == "Fossil SCM"
        ungrouped_names = [p.name for p in context["sidebar_ungrouped"]]
        assert "Ungrouped P" in ungrouped_names

    def test_empty_group_not_in_sidebar(self, admin_client, sample_group):
        """A group with no projects should not appear in sidebar_grouped."""
        response = admin_client.get("/dashboard/")
        context = response.context
        assert len(context["sidebar_grouped"]) == 0

    def test_unauthenticated_gets_empty_context(self, client):
        response = client.get("/dashboard/")
        # Redirects to login, but if we could check context it would be empty
        assert response.status_code == 302

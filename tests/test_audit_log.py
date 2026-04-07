"""Tests for Unified Audit Log: view, permissions, filtering."""

import pytest
from django.contrib.auth.models import Group, Permission

from organization.models import Team
from projects.models import Project


@pytest.fixture
def org_admin_user(db):
    """User with ORGANIZATION_CHANGE permission but not superuser."""
    user = __import__("django.contrib.auth.models", fromlist=["User"]).User.objects.create_user(
        username="orgadmin", email="orgadmin@test.com", password="testpass123"
    )
    group, _ = Group.objects.get_or_create(name="OrgAdmins")
    perms = Permission.objects.filter(
        content_type__app_label="organization",
    )
    group.permissions.set(perms)
    user.groups.add(group)
    return user


@pytest.fixture
def org_admin_client(client, org_admin_user):
    client.login(username="orgadmin", password="testpass123")
    return client


# --- Access Control ---


@pytest.mark.django_db
class TestAuditLogAccess:
    def test_audit_log_accessible_to_superuser(self, admin_client):
        response = admin_client.get("/settings/audit/")
        assert response.status_code == 200
        assert "Audit Log" in response.content.decode()

    def test_audit_log_accessible_to_org_admin(self, org_admin_client):
        response = org_admin_client.get("/settings/audit/")
        assert response.status_code == 200

    def test_audit_log_denied_for_viewer(self, viewer_client):
        response = viewer_client.get("/settings/audit/")
        assert response.status_code == 403

    def test_audit_log_denied_for_no_perm(self, no_perm_client):
        response = no_perm_client.get("/settings/audit/")
        assert response.status_code == 403

    def test_audit_log_denied_for_anon(self, client):
        response = client.get("/settings/audit/")
        assert response.status_code == 302  # Redirect to login


# --- Content ---


@pytest.mark.django_db
class TestAuditLogContent:
    def test_shows_project_history(self, admin_client, admin_user, org):
        Project.objects.create(name="Audit Test Project", organization=org, created_by=admin_user)
        response = admin_client.get("/settings/audit/")
        content = response.content.decode()
        assert "Audit Test Project" in content
        assert "Created" in content

    def test_shows_organization_history(self, admin_client, org):
        response = admin_client.get("/settings/audit/")
        content = response.content.decode()
        assert "Organization" in content

    def test_shows_team_history(self, admin_client, admin_user, org):
        Team.objects.create(name="Audit Test Team", organization=org, created_by=admin_user)
        response = admin_client.get("/settings/audit/")
        content = response.content.decode()
        assert "Audit Test Team" in content

    def test_filter_by_model_type(self, admin_client, admin_user, org):
        Project.objects.create(name="Filter Test", organization=org, created_by=admin_user)
        Team.objects.create(name="Should Not Show", organization=org, created_by=admin_user)
        response = admin_client.get("/settings/audit/?model=Project")
        content = response.content.decode()
        assert "Filter Test" in content
        assert "Should Not Show" not in content

    def test_filter_shows_all_when_no_filter(self, admin_client, admin_user, org):
        Project.objects.create(name="Project Entry", organization=org, created_by=admin_user)
        Team.objects.create(name="Team Entry", organization=org, created_by=admin_user)
        response = admin_client.get("/settings/audit/")
        content = response.content.decode()
        assert "Project Entry" in content
        assert "Team Entry" in content

    def test_audit_log_entries_sorted_by_date(self, admin_client, admin_user, org):
        Project.objects.create(name="First Project", organization=org, created_by=admin_user)
        Project.objects.create(name="Second Project", organization=org, created_by=admin_user)
        response = admin_client.get("/settings/audit/?model=Project")
        entries = response.context["entries"]
        # Most recent first
        project_entries = [e for e in entries if e["model"] == "Project"]
        dates = [e["date"] for e in project_entries]
        assert dates == sorted(dates, reverse=True)

    def test_available_models_in_context(self, admin_client):
        response = admin_client.get("/settings/audit/")
        assert "available_models" in response.context
        assert "Project" in response.context["available_models"]
        assert "Organization" in response.context["available_models"]
        assert "Team" in response.context["available_models"]
        assert "FossilRepository" in response.context["available_models"]

    def test_audit_log_sidebar_link_for_superuser(self, admin_client):
        response = admin_client.get("/dashboard/")
        assert "/settings/audit/" in response.content.decode()

    def test_audit_log_sidebar_link_hidden_for_viewer(self, viewer_client):
        response = viewer_client.get("/dashboard/")
        assert "/settings/audit/" not in response.content.decode()

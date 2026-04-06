import pytest
from django.contrib.auth.models import User

from .models import Organization, OrganizationMember, Team


@pytest.mark.django_db
class TestOrganization:
    def test_create_organization(self):
        org = Organization.objects.create(name="Acme Corp")
        assert org.slug == "acme-corp"
        assert org.guid is not None

    def test_soft_delete_excludes_from_default_manager(self):
        user = User.objects.create_user(username="test", password="x")
        org = Organization.objects.create(name="DeleteMe")
        org.soft_delete(user=user)
        assert Organization.objects.filter(slug="deleteme").count() == 0
        assert Organization.all_objects.filter(slug="deleteme").count() == 1


@pytest.mark.django_db
class TestOrganizationMember:
    def test_create_membership(self, admin_user, org):
        assert OrganizationMember.objects.filter(member=admin_user, organization=org).exists()

    def test_unique_membership(self, admin_user, org):
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            OrganizationMember.objects.create(member=admin_user, organization=org)

    def test_str_representation(self, admin_user, org):
        member = OrganizationMember.objects.get(member=admin_user, organization=org)
        assert str(member) == f"{org}/{admin_user}"


@pytest.mark.django_db
class TestOrgSettingsViews:
    def test_settings_page_renders(self, admin_client, org):
        response = admin_client.get("/settings/")
        assert response.status_code == 200
        assert org.name in response.content.decode()

    def test_settings_denied_without_perm(self, no_perm_client, org):
        response = no_perm_client.get("/settings/")
        assert response.status_code == 403

    def test_settings_edit_renders(self, admin_client, org):
        response = admin_client.get("/settings/edit/")
        assert response.status_code == 200

    def test_settings_edit_saves(self, admin_client, org):
        response = admin_client.post("/settings/edit/", {"name": "Updated Org", "description": "New desc", "website": ""})
        assert response.status_code == 302
        org.refresh_from_db()
        assert org.name == "Updated Org"

    def test_settings_edit_denied(self, no_perm_client, org):
        response = no_perm_client.post("/settings/edit/", {"name": "Hacked"})
        assert response.status_code == 403


@pytest.mark.django_db
class TestMemberViews:
    def test_member_list_renders(self, admin_client, org):
        response = admin_client.get("/settings/members/")
        assert response.status_code == 200

    def test_member_list_htmx_returns_partial(self, admin_client, org):
        response = admin_client.get("/settings/members/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b"member-table" in response.content

    def test_member_list_search(self, admin_client, org):
        response = admin_client.get("/settings/members/?search=admin")
        assert response.status_code == 200

    def test_member_list_denied(self, no_perm_client, org):
        response = no_perm_client.get("/settings/members/")
        assert response.status_code == 403

    def test_member_add(self, admin_client, org):
        User.objects.create_user(username="newuser", password="x")
        response = admin_client.post("/settings/members/add/", {"user": User.objects.get(username="newuser").id})
        assert response.status_code == 302
        assert OrganizationMember.objects.filter(member__username="newuser", organization=org).exists()

    def test_member_add_denied(self, no_perm_client, org):
        response = no_perm_client.get("/settings/members/add/")
        assert response.status_code == 403

    def test_member_remove(self, admin_client, org, admin_user):
        response = admin_client.post(f"/settings/members/{admin_user.username}/remove/")
        assert response.status_code == 302
        membership = OrganizationMember.all_objects.get(member=admin_user, organization=org)
        assert membership.is_deleted

    def test_member_remove_denied(self, no_perm_client, org, admin_user):
        response = no_perm_client.post(f"/settings/members/{admin_user.username}/remove/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestTeamModel:
    def test_create_team(self, org, admin_user):
        team = Team.objects.create(name="Backend", organization=org, created_by=admin_user)
        assert team.slug == "backend"
        assert team.guid is not None

    def test_soft_delete_team(self, sample_team, admin_user):
        sample_team.soft_delete(user=admin_user)
        assert Team.objects.filter(slug=sample_team.slug).count() == 0
        assert Team.all_objects.filter(slug=sample_team.slug).count() == 1


@pytest.mark.django_db
class TestTeamViews:
    def test_team_list_renders(self, admin_client, org, sample_team):
        response = admin_client.get("/settings/teams/")
        assert response.status_code == 200
        assert sample_team.name in response.content.decode()

    def test_team_list_htmx(self, admin_client, org, sample_team):
        response = admin_client.get("/settings/teams/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b"team-table" in response.content

    def test_team_list_search(self, admin_client, org, sample_team):
        response = admin_client.get("/settings/teams/?search=Core")
        assert response.status_code == 200

    def test_team_list_denied(self, no_perm_client, org):
        response = no_perm_client.get("/settings/teams/")
        assert response.status_code == 403

    def test_team_create(self, admin_client, org):
        response = admin_client.post("/settings/teams/create/", {"name": "New Team", "description": "A new team"})
        assert response.status_code == 302
        assert Team.objects.filter(slug="new-team").exists()

    def test_team_create_denied(self, no_perm_client, org):
        response = no_perm_client.post("/settings/teams/create/", {"name": "Hack Team"})
        assert response.status_code == 403

    def test_team_detail_renders(self, admin_client, sample_team):
        response = admin_client.get(f"/settings/teams/{sample_team.slug}/")
        assert response.status_code == 200
        assert sample_team.name in response.content.decode()

    def test_team_update(self, admin_client, sample_team):
        response = admin_client.post(f"/settings/teams/{sample_team.slug}/edit/", {"name": "Updated Team", "description": ""})
        assert response.status_code == 302
        sample_team.refresh_from_db()
        assert sample_team.name == "Updated Team"

    def test_team_update_denied(self, no_perm_client, sample_team):
        response = no_perm_client.post(f"/settings/teams/{sample_team.slug}/edit/", {"name": "Hacked"})
        assert response.status_code == 403

    def test_team_delete(self, admin_client, sample_team):
        response = admin_client.post(f"/settings/teams/{sample_team.slug}/delete/")
        assert response.status_code == 302
        assert Team.objects.filter(slug=sample_team.slug).count() == 0

    def test_team_delete_denied(self, no_perm_client, sample_team):
        response = no_perm_client.post(f"/settings/teams/{sample_team.slug}/delete/")
        assert response.status_code == 403

    def test_team_member_add(self, admin_client, sample_team):
        new_user = User.objects.create_user(username="teamuser", password="x")
        response = admin_client.post(f"/settings/teams/{sample_team.slug}/members/add/", {"user": new_user.id})
        assert response.status_code == 302
        assert sample_team.members.filter(username="teamuser").exists()

    def test_team_member_remove(self, admin_client, sample_team, admin_user):
        response = admin_client.post(f"/settings/teams/{sample_team.slug}/members/{admin_user.username}/remove/")
        assert response.status_code == 302
        assert not sample_team.members.filter(username=admin_user.username).exists()

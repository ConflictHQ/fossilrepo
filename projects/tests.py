import pytest

from .models import Project, ProjectTeam


@pytest.mark.django_db
class TestProjectModel:
    def test_create_project(self, org, admin_user):
        project = Project.objects.create(name="Test Project", organization=org, created_by=admin_user)
        assert project.slug == "test-project"
        assert project.guid is not None
        assert project.visibility == "private"

    def test_soft_delete_project(self, sample_project, admin_user):
        sample_project.soft_delete(user=admin_user)
        assert Project.objects.filter(slug=sample_project.slug).count() == 0
        assert Project.all_objects.filter(slug=sample_project.slug).count() == 1


@pytest.mark.django_db
class TestProjectViews:
    def test_project_list_renders(self, admin_client, sample_project):
        response = admin_client.get("/projects/")
        assert response.status_code == 200
        assert sample_project.name in response.content.decode()

    def test_project_list_htmx(self, admin_client, sample_project):
        response = admin_client.get("/projects/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b"project-table" in response.content

    def test_project_list_search(self, admin_client, sample_project):
        response = admin_client.get("/projects/?search=Frontend")
        assert response.status_code == 200

    def test_project_list_no_perm_sees_public_only(self, no_perm_client, org, admin_user):
        # User without PROJECT_VIEW perm sees only public + internal, not private
        public = Project.objects.create(name="PubProj", organization=org, visibility="public", created_by=admin_user)
        Project.objects.create(name="PrivProj", organization=org, visibility="private", created_by=admin_user)
        response = no_perm_client.get("/projects/")
        assert response.status_code == 200
        body = response.content.decode()
        assert public.name in body
        assert "PrivProj" not in body

    def test_project_create(self, admin_client, org):
        response = admin_client.post("/projects/create/", {"name": "New Project", "description": "Test", "visibility": "private"})
        assert response.status_code == 302
        assert Project.objects.filter(slug="new-project").exists()

    def test_project_create_denied(self, no_perm_client, org):
        response = no_perm_client.post("/projects/create/", {"name": "Hack"})
        assert response.status_code == 403

    def test_project_detail_renders(self, admin_client, sample_project):
        response = admin_client.get(f"/projects/{sample_project.slug}/")
        assert response.status_code == 200
        assert sample_project.name in response.content.decode()

    def test_project_detail_shows_teams(self, admin_client, sample_project, sample_team):
        response = admin_client.get(f"/projects/{sample_project.slug}/")
        assert sample_team.name in response.content.decode()

    def test_project_update(self, admin_client, sample_project):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/edit/",
            {"name": "Updated Project", "description": "Updated", "visibility": "public"},
        )
        assert response.status_code == 302
        sample_project.refresh_from_db()
        assert sample_project.name == "Updated Project"
        assert sample_project.visibility == "public"

    def test_project_update_denied(self, no_perm_client, sample_project):
        response = no_perm_client.post(f"/projects/{sample_project.slug}/edit/", {"name": "Hacked"})
        assert response.status_code == 403

    def test_project_delete(self, admin_client, sample_project):
        response = admin_client.post(f"/projects/{sample_project.slug}/delete/")
        assert response.status_code == 302
        assert Project.objects.filter(slug=sample_project.slug).count() == 0

    def test_project_delete_denied(self, no_perm_client, sample_project):
        response = no_perm_client.post(f"/projects/{sample_project.slug}/delete/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestProjectTeamViews:
    def test_project_team_add(self, admin_client, org, sample_project, admin_user):
        from organization.models import Team

        new_team = Team.objects.create(name="QA Team", organization=org, created_by=admin_user)
        response = admin_client.post(
            f"/projects/{sample_project.slug}/teams/add/",
            {"team": new_team.id, "role": "read"},
        )
        assert response.status_code == 302
        assert ProjectTeam.objects.filter(project=sample_project, team=new_team).exists()

    def test_project_team_add_denied(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/teams/add/")
        assert response.status_code == 403

    def test_project_team_edit(self, admin_client, sample_project, sample_team):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/teams/{sample_team.slug}/edit/",
            {"role": "admin"},
        )
        assert response.status_code == 302
        pt = ProjectTeam.objects.get(project=sample_project, team=sample_team)
        assert pt.role == "admin"

    def test_project_team_edit_denied(self, no_perm_client, sample_project, sample_team):
        response = no_perm_client.post(f"/projects/{sample_project.slug}/teams/{sample_team.slug}/edit/", {"role": "admin"})
        assert response.status_code == 403

    def test_project_team_remove(self, admin_client, sample_project, sample_team):
        response = admin_client.post(f"/projects/{sample_project.slug}/teams/{sample_team.slug}/remove/")
        assert response.status_code == 302
        assert ProjectTeam.objects.filter(project=sample_project, team=sample_team).count() == 0

    def test_project_team_remove_denied(self, no_perm_client, sample_project, sample_team):
        response = no_perm_client.post(f"/projects/{sample_project.slug}/teams/{sample_team.slug}/remove/")
        assert response.status_code == 403

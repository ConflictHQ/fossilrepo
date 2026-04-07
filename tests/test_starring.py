"""Tests for Project Starring: model, toggle view, explore page, and admin."""

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError

from projects.models import Project, ProjectStar

# --- Model Tests ---


@pytest.mark.django_db
class TestProjectStarModel:
    def test_create_star(self, admin_user, sample_project):
        star = ProjectStar.objects.create(user=admin_user, project=sample_project)
        assert star.pk is not None
        assert star.user == admin_user
        assert star.project == sample_project
        assert str(star) == f"{admin_user} starred {sample_project}"

    def test_unique_constraint(self, admin_user, sample_project):
        ProjectStar.objects.create(user=admin_user, project=sample_project)
        with pytest.raises(IntegrityError):
            ProjectStar.objects.create(user=admin_user, project=sample_project)

    def test_star_count_property(self, admin_user, viewer_user, sample_project):
        assert sample_project.star_count == 0
        ProjectStar.objects.create(user=admin_user, project=sample_project)
        assert sample_project.star_count == 1
        ProjectStar.objects.create(user=viewer_user, project=sample_project)
        assert sample_project.star_count == 2

    def test_star_cascade_on_user_delete(self, org, admin_user):
        """Stars cascade-delete when the user is deleted."""
        temp_user = User.objects.create_user(username="tempuser", password="testpass123")
        project = Project.objects.create(name="Cascade Test", organization=org, created_by=admin_user)
        ProjectStar.objects.create(user=temp_user, project=project)
        temp_user.delete()
        assert ProjectStar.objects.count() == 0

    def test_multiple_users_can_star_same_project(self, admin_user, viewer_user, sample_project):
        ProjectStar.objects.create(user=admin_user, project=sample_project)
        ProjectStar.objects.create(user=viewer_user, project=sample_project)
        assert ProjectStar.objects.filter(project=sample_project).count() == 2


# --- Toggle Star View Tests ---


@pytest.mark.django_db
class TestToggleStarView:
    def test_star_project(self, admin_client, admin_user, sample_project):
        response = admin_client.post(f"/projects/{sample_project.slug}/star/")
        assert response.status_code == 302
        assert ProjectStar.objects.filter(user=admin_user, project=sample_project).exists()

    def test_unstar_project(self, admin_client, admin_user, sample_project):
        ProjectStar.objects.create(user=admin_user, project=sample_project)
        response = admin_client.post(f"/projects/{sample_project.slug}/star/")
        assert response.status_code == 302
        assert not ProjectStar.objects.filter(user=admin_user, project=sample_project).exists()

    def test_star_htmx_returns_partial(self, admin_client, admin_user, sample_project):
        response = admin_client.post(f"/projects/{sample_project.slug}/star/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        content = response.content.decode()
        assert "star-button" in content
        assert "Starred" in content  # Just starred it
        assert "<!DOCTYPE html>" not in content

    def test_unstar_htmx_returns_partial(self, admin_client, admin_user, sample_project):
        ProjectStar.objects.create(user=admin_user, project=sample_project)
        response = admin_client.post(f"/projects/{sample_project.slug}/star/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Star" in content

    def test_star_denied_for_anon(self, client, sample_project):
        response = client.post(f"/projects/{sample_project.slug}/star/")
        assert response.status_code == 302  # Redirect to login

    def test_star_404_for_deleted_project(self, admin_client, admin_user, sample_project):
        sample_project.soft_delete(user=admin_user)
        response = admin_client.post(f"/projects/{sample_project.slug}/star/")
        assert response.status_code == 404

    def test_star_shows_on_project_detail(self, admin_client, admin_user, sample_project):
        ProjectStar.objects.create(user=admin_user, project=sample_project)
        response = admin_client.get(f"/projects/{sample_project.slug}/")
        assert response.status_code == 200
        assert response.context["is_starred"] is True

    def test_unstarred_shows_on_project_detail(self, admin_client, admin_user, sample_project):
        response = admin_client.get(f"/projects/{sample_project.slug}/")
        assert response.status_code == 200
        assert response.context["is_starred"] is False


# --- Explore View Tests ---


@pytest.mark.django_db
class TestExploreView:
    def test_explore_accessible_to_anon(self, client, org, admin_user):
        Project.objects.create(name="Public Project", organization=org, visibility="public", created_by=admin_user)
        response = client.get("/explore/")
        assert response.status_code == 200
        assert "Public Project" in response.content.decode()

    def test_explore_anon_only_sees_public(self, client, org, admin_user):
        Project.objects.create(name="Public One", organization=org, visibility="public", created_by=admin_user)
        Project.objects.create(name="Internal One", organization=org, visibility="internal", created_by=admin_user)
        Project.objects.create(name="Private One", organization=org, visibility="private", created_by=admin_user)
        response = client.get("/explore/")
        content = response.content.decode()
        assert "Public One" in content
        assert "Internal One" not in content
        assert "Private One" not in content

    def test_explore_authenticated_sees_public_and_internal(self, admin_client, org, admin_user):
        Project.objects.create(name="Public Two", organization=org, visibility="public", created_by=admin_user)
        Project.objects.create(name="Internal Two", organization=org, visibility="internal", created_by=admin_user)
        Project.objects.create(name="Private Two", organization=org, visibility="private", created_by=admin_user)
        response = admin_client.get("/explore/")
        # Check the explore queryset in context (not full page content, which includes sidebar)
        explore_project_names = [p.name for p in response.context["projects"]]
        assert "Public Two" in explore_project_names
        assert "Internal Two" in explore_project_names
        assert "Private Two" not in explore_project_names

    def test_explore_sort_by_name(self, client, org, admin_user):
        Project.objects.create(name="Zebra", organization=org, visibility="public", created_by=admin_user)
        Project.objects.create(name="Alpha", organization=org, visibility="public", created_by=admin_user)
        response = client.get("/explore/?sort=name")
        content = response.content.decode()
        assert content.index("Alpha") < content.index("Zebra")

    def test_explore_sort_by_stars(self, client, org, admin_user):
        p1 = Project.objects.create(name="Less Stars", organization=org, visibility="public", created_by=admin_user)
        p2 = Project.objects.create(name="More Stars", organization=org, visibility="public", created_by=admin_user)
        user1 = User.objects.create_user(username="u1", password="testpass123")
        user2 = User.objects.create_user(username="u2", password="testpass123")
        ProjectStar.objects.create(user=user1, project=p2)
        ProjectStar.objects.create(user=user2, project=p2)
        ProjectStar.objects.create(user=user1, project=p1)
        response = client.get("/explore/?sort=stars")
        content = response.content.decode()
        assert content.index("More Stars") < content.index("Less Stars")

    def test_explore_sort_by_recent(self, client, org, admin_user):
        Project.objects.create(name="Old Project", organization=org, visibility="public", created_by=admin_user)
        Project.objects.create(name="New Project", organization=org, visibility="public", created_by=admin_user)
        response = client.get("/explore/?sort=recent")
        content = response.content.decode()
        assert content.index("New Project") < content.index("Old Project")

    def test_explore_search(self, client, org, admin_user):
        Project.objects.create(name="Fossil SCM", organization=org, visibility="public", created_by=admin_user)
        Project.objects.create(name="Other Project", organization=org, visibility="public", created_by=admin_user)
        response = client.get("/explore/?search=fossil")
        content = response.content.decode()
        assert "Fossil SCM" in content
        assert "Other Project" not in content

    def test_explore_excludes_deleted_projects(self, client, org, admin_user):
        project = Project.objects.create(name="Deleted Project", organization=org, visibility="public", created_by=admin_user)
        project.soft_delete(user=admin_user)
        response = client.get("/explore/")
        assert "Deleted Project" not in response.content.decode()

    def test_explore_starred_ids_for_authenticated_user(self, admin_client, admin_user, org):
        p1 = Project.objects.create(name="Starred P", organization=org, visibility="public", created_by=admin_user)
        Project.objects.create(name="Unstarred P", organization=org, visibility="public", created_by=admin_user)
        ProjectStar.objects.create(user=admin_user, project=p1)
        response = admin_client.get("/explore/")
        assert p1.id in response.context["starred_ids"]

    def test_explore_sidebar_link_exists(self, admin_client):
        response = admin_client.get("/dashboard/")
        assert response.status_code == 200
        assert "/explore/" in response.content.decode()

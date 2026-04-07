import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.branch_protection import BranchProtection
from fossil.models import FossilRepository
from organization.models import Team
from projects.models import ProjectTeam


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def protection_rule(fossil_repo_obj, admin_user):
    return BranchProtection.objects.create(
        repository=fossil_repo_obj,
        branch_pattern="trunk",
        require_status_checks=True,
        required_contexts="ci/tests\nci/lint",
        restrict_push=True,
        created_by=admin_user,
    )


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """User with write access but not admin."""
    writer = User.objects.create_user(username="writer_bp", password="testpass123")
    team = Team.objects.create(name="BP Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="writer_bp", password="testpass123")
    return client


# --- BranchProtection Model Tests ---


@pytest.mark.django_db
class TestBranchProtectionModel:
    def test_create_rule(self, protection_rule):
        assert protection_rule.pk is not None
        assert str(protection_rule) == f"trunk ({protection_rule.repository})"

    def test_soft_delete(self, protection_rule, admin_user):
        protection_rule.soft_delete(user=admin_user)
        assert protection_rule.is_deleted
        assert BranchProtection.objects.filter(pk=protection_rule.pk).count() == 0
        assert BranchProtection.all_objects.filter(pk=protection_rule.pk).count() == 1

    def test_unique_together(self, fossil_repo_obj, admin_user):
        BranchProtection.objects.create(
            repository=fossil_repo_obj,
            branch_pattern="release-*",
            created_by=admin_user,
        )
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            BranchProtection.objects.create(
                repository=fossil_repo_obj,
                branch_pattern="release-*",
                created_by=admin_user,
            )

    def test_ordering(self, fossil_repo_obj, admin_user):
        r1 = BranchProtection.objects.create(repository=fossil_repo_obj, branch_pattern="trunk", created_by=admin_user)
        r2 = BranchProtection.objects.create(repository=fossil_repo_obj, branch_pattern="develop", created_by=admin_user)
        rules = list(BranchProtection.objects.filter(repository=fossil_repo_obj))
        # Ordered by branch_pattern alphabetically
        assert rules[0] == r2
        assert rules[1] == r1

    def test_get_required_contexts_list(self, protection_rule):
        contexts = protection_rule.get_required_contexts_list()
        assert contexts == ["ci/tests", "ci/lint"]

    def test_get_required_contexts_list_empty(self, fossil_repo_obj, admin_user):
        rule = BranchProtection.objects.create(
            repository=fossil_repo_obj,
            branch_pattern="feature-*",
            required_contexts="",
            created_by=admin_user,
        )
        assert rule.get_required_contexts_list() == []

    def test_get_required_contexts_list_filters_blanks(self, fossil_repo_obj, admin_user):
        rule = BranchProtection.objects.create(
            repository=fossil_repo_obj,
            branch_pattern="hotfix-*",
            required_contexts="ci/tests\n\n  \nci/lint\n",
            created_by=admin_user,
        )
        assert rule.get_required_contexts_list() == ["ci/tests", "ci/lint"]


# --- Branch Protection List View Tests ---


@pytest.mark.django_db
class TestBranchProtectionListView:
    def test_list_rules(self, admin_client, sample_project, protection_rule):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/branches/protect/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "trunk" in content
        assert "CI required" in content
        assert "Push restricted" in content

    def test_list_empty(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/branches/protect/")
        assert response.status_code == 200
        assert "No branch protection rules configured" in response.content.decode()

    def test_list_denied_for_writer(self, writer_client, sample_project, protection_rule):
        response = writer_client.get(f"/projects/{sample_project.slug}/fossil/branches/protect/")
        assert response.status_code == 403

    def test_list_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/branches/protect/")
        assert response.status_code == 403

    def test_list_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/branches/protect/")
        assert response.status_code == 302  # redirect to login


# --- Branch Protection Create View Tests ---


@pytest.mark.django_db
class TestBranchProtectionCreateView:
    def test_get_form(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/branches/protect/create/")
        assert response.status_code == 200
        assert "Create Branch Protection Rule" in response.content.decode()

    def test_create_rule(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/branches/protect/create/",
            {
                "branch_pattern": "develop",
                "require_status_checks": "on",
                "required_contexts": "ci/tests",
                "restrict_push": "on",
            },
        )
        assert response.status_code == 302
        rule = BranchProtection.objects.get(branch_pattern="develop")
        assert rule.require_status_checks is True
        assert rule.required_contexts == "ci/tests"
        assert rule.restrict_push is True

    def test_create_without_pattern_fails(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/branches/protect/create/",
            {"branch_pattern": ""},
        )
        assert response.status_code == 200  # Re-renders form
        assert BranchProtection.objects.count() == 0

    def test_create_duplicate_pattern_fails(self, admin_client, sample_project, fossil_repo_obj, protection_rule):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/branches/protect/create/",
            {"branch_pattern": "trunk"},
        )
        assert response.status_code == 200  # Re-renders form with error
        assert "already exists" in response.content.decode()

    def test_create_denied_for_writer(self, writer_client, sample_project):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/branches/protect/create/",
            {"branch_pattern": "evil-branch"},
        )
        assert response.status_code == 403


# --- Branch Protection Edit View Tests ---


@pytest.mark.django_db
class TestBranchProtectionEditView:
    def test_get_edit_form(self, admin_client, sample_project, protection_rule):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/branches/protect/{protection_rule.pk}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "trunk" in content
        assert "Update Rule" in content

    def test_edit_rule(self, admin_client, sample_project, protection_rule):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/branches/protect/{protection_rule.pk}/edit/",
            {
                "branch_pattern": "trunk",
                "require_status_checks": "on",
                "required_contexts": "ci/tests\nci/lint\nci/build",
                "restrict_push": "on",
            },
        )
        assert response.status_code == 302
        protection_rule.refresh_from_db()
        assert "ci/build" in protection_rule.required_contexts

    def test_edit_change_pattern(self, admin_client, sample_project, protection_rule):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/branches/protect/{protection_rule.pk}/edit/",
            {"branch_pattern": "main", "restrict_push": "on"},
        )
        assert response.status_code == 302
        protection_rule.refresh_from_db()
        assert protection_rule.branch_pattern == "main"

    def test_edit_denied_for_writer(self, writer_client, sample_project, protection_rule):
        response = writer_client.post(
            f"/projects/{sample_project.slug}/fossil/branches/protect/{protection_rule.pk}/edit/",
            {"branch_pattern": "evil-branch"},
        )
        assert response.status_code == 403

    def test_edit_nonexistent_rule(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/branches/protect/99999/edit/")
        assert response.status_code == 404


# --- Branch Protection Delete View Tests ---


@pytest.mark.django_db
class TestBranchProtectionDeleteView:
    def test_delete_rule(self, admin_client, sample_project, protection_rule):
        response = admin_client.post(f"/projects/{sample_project.slug}/fossil/branches/protect/{protection_rule.pk}/delete/")
        assert response.status_code == 302
        protection_rule.refresh_from_db()
        assert protection_rule.is_deleted

    def test_delete_get_redirects(self, admin_client, sample_project, protection_rule):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/branches/protect/{protection_rule.pk}/delete/")
        assert response.status_code == 302  # GET redirects to list

    def test_delete_denied_for_writer(self, writer_client, sample_project, protection_rule):
        response = writer_client.post(f"/projects/{sample_project.slug}/fossil/branches/protect/{protection_rule.pk}/delete/")
        assert response.status_code == 403

    def test_delete_nonexistent_rule(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(f"/projects/{sample_project.slug}/fossil/branches/protect/99999/delete/")
        assert response.status_code == 404

import pytest
from django.contrib.auth.models import Group, Permission, User

from organization.models import Organization, OrganizationMember, Team
from pages.models import Page
from projects.models import Project, ProjectTeam


@pytest.fixture
def admin_user(db):
    user = User.objects.create_superuser(username="admin", email="admin@test.com", password="testpass123")
    return user


@pytest.fixture
def viewer_user(db):
    user = User.objects.create_user(username="viewer", email="viewer@test.com", password="testpass123")
    group, _ = Group.objects.get_or_create(name="Viewers")
    view_perms = Permission.objects.filter(
        content_type__app_label__in=["items", "organization", "projects", "pages"],
        codename__startswith="view_",
    )
    group.permissions.set(view_perms)
    user.groups.add(group)
    return user


@pytest.fixture
def no_perm_user(db):
    return User.objects.create_user(username="noperm", email="noperm@test.com", password="testpass123")


@pytest.fixture
def org(db, admin_user):
    org = Organization.objects.create(name="Test Org", created_by=admin_user)
    OrganizationMember.objects.create(member=admin_user, organization=org)
    return org


@pytest.fixture
def sample_team(db, org, admin_user):
    team = Team.objects.create(name="Core Devs", organization=org, created_by=admin_user)
    team.members.add(admin_user)
    return team


@pytest.fixture
def sample_project(db, org, admin_user, sample_team):
    project = Project.objects.create(name="Frontend App", organization=org, visibility="private", created_by=admin_user)
    ProjectTeam.objects.create(project=project, team=sample_team, role="write", created_by=admin_user)
    return project


@pytest.fixture
def sample_page(db, org, admin_user):
    return Page.objects.create(
        name="Getting Started",
        content="# Getting Started\n\nWelcome to the docs.",
        organization=org,
        created_by=admin_user,
    )


@pytest.fixture
def fossil_repo(db, sample_project, admin_user, tmp_path):
    """Create a FossilRepository with a real .fossil file for testing."""
    import shutil

    from fossil.models import FossilRepository

    # Copy a test repo to tmp_path
    src = "/tmp/fossil-setup/frontend-app.fossil"
    dest = tmp_path / "test-project.fossil"
    shutil.copy2(src, dest)

    # Override FOSSIL_DATA_DIR for this test

    repo = FossilRepository.objects.create(
        project=sample_project,
        filename="test-project.fossil",
        created_by=admin_user,
    )
    # Patch the full_path property to point to our tmp file
    repo._test_path = dest
    return repo


@pytest.fixture
def admin_client(client, admin_user):
    client.login(username="admin", password="testpass123")
    return client


@pytest.fixture
def viewer_client(client, viewer_user):
    client.login(username="viewer", password="testpass123")
    return client


@pytest.fixture
def no_perm_client(client, no_perm_user):
    client.login(username="noperm", password="testpass123")
    return client

import pytest
from django.contrib.auth.models import Group, Permission, User

from organization.models import Organization, OrganizationMember


@pytest.fixture
def admin_user(db):
    user = User.objects.create_superuser(username="admin", email="admin@test.com", password="testpass123")
    return user


@pytest.fixture
def viewer_user(db):
    user = User.objects.create_user(username="viewer", email="viewer@test.com", password="testpass123")
    group, _ = Group.objects.get_or_create(name="Viewers")
    view_perms = Permission.objects.filter(content_type__app_label="items", codename__startswith="view_")
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

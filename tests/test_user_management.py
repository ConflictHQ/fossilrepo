import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from organization.models import OrganizationMember


@pytest.fixture
def org_admin_user(db, org):
    """A non-superuser who has ORGANIZATION_CHANGE permission via group."""
    from django.contrib.auth.models import Group, Permission

    user = User.objects.create_user(username="orgadmin", email="orgadmin@test.com", password="testpass123")
    group, _ = Group.objects.get_or_create(name="OrgAdmins")
    change_perm = Permission.objects.get(content_type__app_label="organization", codename="change_organization")
    view_perm = Permission.objects.get(content_type__app_label="organization", codename="view_organizationmember")
    group.permissions.add(change_perm, view_perm)
    user.groups.add(group)
    OrganizationMember.objects.create(member=user, organization=org)
    return user


@pytest.fixture
def org_admin_client(org_admin_user):
    c = Client()
    c.login(username="orgadmin", password="testpass123")
    return c


@pytest.fixture
def target_user(db, org, admin_user):
    """A regular user who is an org member, to be the target of management actions."""
    user = User.objects.create_user(
        username="targetuser", email="target@test.com", password="testpass123", first_name="Target", last_name="User"
    )
    OrganizationMember.objects.create(member=user, organization=org, created_by=admin_user)
    return user


# --- user_create ---


@pytest.mark.django_db
class TestUserCreate:
    def test_get_form(self, admin_client):
        response = admin_client.get(reverse("organization:user_create"))
        assert response.status_code == 200
        assert "New User" in response.content.decode()

    def test_create_user(self, admin_client, org):
        response = admin_client.post(
            reverse("organization:user_create"),
            {
                "username": "newuser",
                "email": "new@test.com",
                "first_name": "New",
                "last_name": "User",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
            },
        )
        assert response.status_code == 302
        user = User.objects.get(username="newuser")
        assert user.email == "new@test.com"
        assert user.first_name == "New"
        assert user.check_password("Str0ng!Pass99")
        # Verify auto-added as org member
        assert OrganizationMember.objects.filter(member=user, organization=org, deleted_at__isnull=True).exists()

    def test_create_password_mismatch(self, admin_client):
        response = admin_client.post(
            reverse("organization:user_create"),
            {
                "username": "baduser",
                "email": "bad@test.com",
                "password1": "Str0ng!Pass99",
                "password2": "differentpass",
            },
        )
        assert response.status_code == 200
        assert not User.objects.filter(username="baduser").exists()

    def test_create_duplicate_username(self, admin_client, target_user):
        response = admin_client.post(
            reverse("organization:user_create"),
            {
                "username": "targetuser",
                "email": "dup@test.com",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
            },
        )
        assert response.status_code == 200  # Form re-rendered with errors

    def test_create_denied_for_viewer(self, viewer_client):
        response = viewer_client.get(reverse("organization:user_create"))
        assert response.status_code == 403

    def test_create_denied_for_anon(self, client):
        response = client.get(reverse("organization:user_create"))
        assert response.status_code == 302  # Redirect to login

    def test_create_allowed_for_org_admin(self, org_admin_client, org):
        response = org_admin_client.post(
            reverse("organization:user_create"),
            {
                "username": "orgcreated",
                "email": "orgcreated@test.com",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
            },
        )
        assert response.status_code == 302
        assert User.objects.filter(username="orgcreated").exists()


# --- user_detail ---


@pytest.mark.django_db
class TestUserDetail:
    def test_view_user(self, admin_client, target_user):
        response = admin_client.get(reverse("organization:user_detail", kwargs={"username": "targetuser"}))
        assert response.status_code == 200
        content = response.content.decode()
        assert "targetuser" in content
        assert "target@test.com" in content
        assert "Target User" in content

    def test_view_shows_teams(self, admin_client, target_user, sample_team):
        sample_team.members.add(target_user)
        response = admin_client.get(reverse("organization:user_detail", kwargs={"username": "targetuser"}))
        assert response.status_code == 200
        assert "Core Devs" in response.content.decode()

    def test_view_denied_for_no_perm(self, no_perm_client, target_user):
        response = no_perm_client.get(reverse("organization:user_detail", kwargs={"username": "targetuser"}))
        assert response.status_code == 403

    def test_view_allowed_for_viewer(self, viewer_client, target_user):
        response = viewer_client.get(reverse("organization:user_detail", kwargs={"username": "targetuser"}))
        assert response.status_code == 200

    def test_view_404_for_missing_user(self, admin_client):
        response = admin_client.get(reverse("organization:user_detail", kwargs={"username": "nonexistent"}))
        assert response.status_code == 404


# --- user_edit ---


@pytest.mark.django_db
class TestUserEdit:
    def test_get_edit_form(self, admin_client, target_user):
        response = admin_client.get(reverse("organization:user_edit", kwargs={"username": "targetuser"}))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Edit targetuser" in content

    def test_edit_user(self, admin_client, target_user):
        response = admin_client.post(
            reverse("organization:user_edit", kwargs={"username": "targetuser"}),
            {
                "email": "updated@test.com",
                "first_name": "Updated",
                "last_name": "Name",
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        target_user.refresh_from_db()
        assert target_user.email == "updated@test.com"
        assert target_user.first_name == "Updated"

    def test_edit_deactivate_user(self, admin_client, target_user):
        response = admin_client.post(
            reverse("organization:user_edit", kwargs={"username": "targetuser"}),
            {
                "email": "target@test.com",
                "first_name": "Target",
                "last_name": "User",
                # is_active omitted = False for checkbox
            },
        )
        assert response.status_code == 302
        target_user.refresh_from_db()
        assert target_user.is_active is False

    def test_edit_self_cannot_deactivate(self, admin_client, admin_user):
        """Superuser editing themselves should not be able to toggle is_active."""
        response = admin_client.post(
            reverse("organization:user_edit", kwargs={"username": "admin"}),
            {
                "email": "admin@test.com",
                "first_name": "Admin",
                "last_name": "",
                # is_active omitted -- but field is disabled so value comes from instance
            },
        )
        assert response.status_code == 302
        admin_user.refresh_from_db()
        # Should still be active because the field was disabled
        assert admin_user.is_active is True

    def test_edit_denied_for_viewer(self, viewer_client, target_user):
        response = viewer_client.get(reverse("organization:user_edit", kwargs={"username": "targetuser"}))
        assert response.status_code == 403

    def test_edit_denied_for_no_perm(self, no_perm_client, target_user):
        response = no_perm_client.get(reverse("organization:user_edit", kwargs={"username": "targetuser"}))
        assert response.status_code == 403

    def test_edit_allowed_for_org_admin(self, org_admin_client, target_user):
        response = org_admin_client.post(
            reverse("organization:user_edit", kwargs={"username": "targetuser"}),
            {
                "email": "orgadminedit@test.com",
                "first_name": "Org",
                "last_name": "Edited",
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        target_user.refresh_from_db()
        assert target_user.email == "orgadminedit@test.com"


# --- user_password ---


@pytest.mark.django_db
class TestUserPassword:
    def test_get_password_form(self, admin_client, target_user):
        response = admin_client.get(reverse("organization:user_password", kwargs={"username": "targetuser"}))
        assert response.status_code == 200
        assert "Change Password" in response.content.decode()

    def test_change_password(self, admin_client, target_user):
        response = admin_client.post(
            reverse("organization:user_password", kwargs={"username": "targetuser"}),
            {
                "new_password1": "NewStr0ng!Pass99",
                "new_password2": "NewStr0ng!Pass99",
            },
        )
        assert response.status_code == 302
        target_user.refresh_from_db()
        assert target_user.check_password("NewStr0ng!Pass99")

    def test_change_password_mismatch(self, admin_client, target_user):
        response = admin_client.post(
            reverse("organization:user_password", kwargs={"username": "targetuser"}),
            {
                "new_password1": "NewStr0ng!Pass99",
                "new_password2": "different",
            },
        )
        assert response.status_code == 200  # Form re-rendered
        target_user.refresh_from_db()
        assert target_user.check_password("testpass123")  # Unchanged

    def test_change_own_password(self, target_user):
        """A regular user (no special perms) can change their own password."""
        c = Client()
        c.login(username="targetuser", password="testpass123")
        response = c.post(
            reverse("organization:user_password", kwargs={"username": "targetuser"}),
            {
                "new_password1": "MyNewStr0ng!Pass99",
                "new_password2": "MyNewStr0ng!Pass99",
            },
        )
        assert response.status_code == 302
        target_user.refresh_from_db()
        assert target_user.check_password("MyNewStr0ng!Pass99")

    def test_change_other_password_denied_for_no_perm(self, no_perm_client, target_user):
        response = no_perm_client.post(
            reverse("organization:user_password", kwargs={"username": "targetuser"}),
            {
                "new_password1": "HackedStr0ng!Pass99",
                "new_password2": "HackedStr0ng!Pass99",
            },
        )
        assert response.status_code == 403
        target_user.refresh_from_db()
        assert target_user.check_password("testpass123")  # Unchanged

    def test_change_other_password_denied_for_viewer(self, viewer_client, target_user):
        response = viewer_client.post(
            reverse("organization:user_password", kwargs={"username": "targetuser"}),
            {
                "new_password1": "HackedStr0ng!Pass99",
                "new_password2": "HackedStr0ng!Pass99",
            },
        )
        assert response.status_code == 403

    def test_change_password_denied_for_anon(self, client, target_user):
        response = client.get(reverse("organization:user_password", kwargs={"username": "targetuser"}))
        assert response.status_code == 302  # Redirect to login

    def test_change_other_password_allowed_for_org_admin(self, org_admin_client, target_user):
        response = org_admin_client.post(
            reverse("organization:user_password", kwargs={"username": "targetuser"}),
            {
                "new_password1": "OrgAdminSet!Pass99",
                "new_password2": "OrgAdminSet!Pass99",
            },
        )
        assert response.status_code == 302
        target_user.refresh_from_db()
        assert target_user.check_password("OrgAdminSet!Pass99")


# --- member_list updates ---


@pytest.mark.django_db
class TestMemberListUpdates:
    def test_usernames_are_clickable_links(self, admin_client, org, admin_user):
        response = admin_client.get(reverse("organization:members"))
        assert response.status_code == 200
        content = response.content.decode()
        expected_url = reverse("organization:user_detail", kwargs={"username": admin_user.username})
        assert expected_url in content

    def test_create_user_button_visible_for_superuser(self, admin_client, org):
        response = admin_client.get(reverse("organization:members"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Create User" in content

    def test_create_user_button_hidden_for_viewer(self, viewer_client, org):
        response = viewer_client.get(reverse("organization:members"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Create User" not in content

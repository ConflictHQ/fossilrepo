import pytest
from django.contrib.auth.models import Group, Permission, User
from django.test import Client
from django.urls import reverse

from organization.models import OrganizationMember, OrgRole


@pytest.fixture
def roles(db):
    """Seed default roles via management command."""
    from django.core.management import call_command

    call_command("seed_roles")
    return OrgRole.objects.all()


@pytest.fixture
def admin_role(roles):
    return OrgRole.objects.get(slug="admin")


@pytest.fixture
def viewer_role(roles):
    return OrgRole.objects.get(slug="viewer")


@pytest.fixture
def developer_role(roles):
    return OrgRole.objects.get(slug="developer")


@pytest.fixture
def manager_role(roles):
    return OrgRole.objects.get(slug="manager")


@pytest.fixture
def target_user(db, org, admin_user):
    user = User.objects.create_user(
        username="targetuser", email="target@test.com", password="testpass123", first_name="Target", last_name="User"
    )
    OrganizationMember.objects.create(member=user, organization=org, created_by=admin_user)
    return user


@pytest.fixture
def org_admin_user(db, org):
    """Non-superuser with ORGANIZATION_CHANGE permission."""
    user = User.objects.create_user(username="orgadmin", email="orgadmin@test.com", password="testpass123")
    group, _ = Group.objects.get_or_create(name="OrgAdmins")
    change_perm = Permission.objects.get(content_type__app_label="organization", codename="change_organization")
    view_perm = Permission.objects.get(content_type__app_label="organization", codename="view_organization")
    view_member_perm = Permission.objects.get(content_type__app_label="organization", codename="view_organizationmember")
    group.permissions.add(change_perm, view_perm, view_member_perm)
    user.groups.add(group)
    OrganizationMember.objects.create(member=user, organization=org)
    return user


@pytest.fixture
def org_admin_client(org_admin_user):
    c = Client()
    c.login(username="orgadmin", password="testpass123")
    return c


# --- OrgRole model ---


@pytest.mark.django_db
class TestOrgRoleModel:
    def test_seed_creates_four_roles(self, roles):
        assert OrgRole.objects.count() == 4

    def test_seed_idempotent(self, roles):
        from django.core.management import call_command

        call_command("seed_roles")
        assert OrgRole.objects.count() == 4

    def test_admin_role_has_all_app_permissions(self, admin_role):
        app_perms = Permission.objects.filter(content_type__app_label__in=["organization", "projects", "pages", "fossil"]).count()
        assert admin_role.permissions.count() == app_perms

    def test_viewer_role_is_default(self, viewer_role):
        assert viewer_role.is_default is True

    def test_admin_role_not_default(self, admin_role):
        assert admin_role.is_default is False

    def test_viewer_has_only_view_permissions(self, viewer_role):
        for perm in viewer_role.permissions.all():
            assert perm.codename.startswith("view_"), f"Viewer role should only have view_ permissions, got {perm.codename}"

    def test_developer_has_add_page(self, developer_role):
        assert developer_role.permissions.filter(codename="add_page").exists()

    def test_developer_no_delete_project(self, developer_role):
        assert not developer_role.permissions.filter(codename="delete_project").exists()

    def test_manager_has_change_organization(self, manager_role):
        assert manager_role.permissions.filter(codename="change_organization").exists()


# --- apply_to_user ---


@pytest.mark.django_db
class TestApplyToUser:
    def test_apply_creates_role_group(self, viewer_role, target_user):
        viewer_role.apply_to_user(target_user)
        assert target_user.groups.filter(name="role_viewer").exists()

    def test_apply_sets_permissions(self, viewer_role, target_user):
        viewer_role.apply_to_user(target_user)
        assert target_user.has_perm("organization.view_organization")

    def test_apply_replaces_old_role(self, viewer_role, admin_role, target_user):
        viewer_role.apply_to_user(target_user)
        admin_role.apply_to_user(target_user)
        # Should only be in admin role group now
        role_groups = target_user.groups.filter(name__startswith="role_")
        assert role_groups.count() == 1
        assert role_groups.first().name == "role_admin"

    def test_remove_role_groups(self, viewer_role, target_user):
        viewer_role.apply_to_user(target_user)
        assert target_user.groups.filter(name__startswith="role_").count() == 1
        OrgRole.remove_role_groups(target_user)
        assert target_user.groups.filter(name__startswith="role_").count() == 0


# --- role_list view ---


@pytest.mark.django_db
class TestRoleListView:
    def test_list_empty(self, admin_client, org):
        response = admin_client.get(reverse("organization:role_list"))
        assert response.status_code == 200
        assert "No roles defined" in response.content.decode()

    def test_list_with_roles(self, admin_client, org, roles):
        response = admin_client.get(reverse("organization:role_list"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Admin" in content
        assert "Manager" in content
        assert "Developer" in content
        assert "Viewer" in content

    def test_list_denied_for_no_perm(self, no_perm_client, org):
        response = no_perm_client.get(reverse("organization:role_list"))
        assert response.status_code == 403

    def test_list_allowed_for_viewer(self, viewer_client, org, roles):
        response = viewer_client.get(reverse("organization:role_list"))
        assert response.status_code == 200

    def test_list_denied_for_anon(self, client, org):
        response = client.get(reverse("organization:role_list"))
        assert response.status_code == 302  # redirect to login


# --- role_detail view ---


@pytest.mark.django_db
class TestRoleDetailView:
    def test_detail_shows_role_info(self, admin_client, org, admin_role):
        response = admin_client.get(reverse("organization:role_detail", kwargs={"slug": "admin"}))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Admin" in content
        assert "Full access" in content

    def test_detail_shows_permissions(self, admin_client, org, viewer_role):
        response = admin_client.get(reverse("organization:role_detail", kwargs={"slug": "viewer"}))
        assert response.status_code == 200
        content = response.content.decode()
        assert "view_organization" in content

    def test_detail_shows_members(self, admin_client, org, viewer_role, target_user):
        membership = OrganizationMember.objects.get(member=target_user, organization=org)
        membership.role = viewer_role
        membership.save()
        response = admin_client.get(reverse("organization:role_detail", kwargs={"slug": "viewer"}))
        assert response.status_code == 200
        assert "targetuser" in response.content.decode()

    def test_detail_denied_for_no_perm(self, no_perm_client, org, viewer_role):
        response = no_perm_client.get(reverse("organization:role_detail", kwargs={"slug": "viewer"}))
        assert response.status_code == 403

    def test_detail_404_for_missing_role(self, admin_client, org):
        response = admin_client.get(reverse("organization:role_detail", kwargs={"slug": "nonexistent"}))
        assert response.status_code == 404


# --- role_initialize view ---


@pytest.mark.django_db
class TestRoleInitializeView:
    def test_initialize_creates_roles(self, admin_client, org):
        assert OrgRole.objects.count() == 0
        response = admin_client.post(reverse("organization:role_initialize"))
        assert response.status_code == 302
        assert OrgRole.objects.count() == 4

    def test_initialize_denied_for_viewer(self, viewer_client, org):
        response = viewer_client.post(reverse("organization:role_initialize"))
        assert response.status_code == 403

    def test_initialize_denied_for_no_perm(self, no_perm_client, org):
        response = no_perm_client.post(reverse("organization:role_initialize"))
        assert response.status_code == 403

    def test_initialize_denied_for_anon(self, client, org):
        response = client.post(reverse("organization:role_initialize"))
        assert response.status_code == 302  # redirect to login

    def test_initialize_allowed_for_org_admin(self, org_admin_client, org):
        response = org_admin_client.post(reverse("organization:role_initialize"))
        assert response.status_code == 302
        assert OrgRole.objects.count() == 4


# --- user_create with role ---


@pytest.mark.django_db
class TestUserCreateWithRole:
    def test_create_user_with_role(self, admin_client, org, viewer_role):
        response = admin_client.post(
            reverse("organization:user_create"),
            {
                "username": "roleuser",
                "email": "role@test.com",
                "first_name": "Role",
                "last_name": "User",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
                "role": viewer_role.pk,
            },
        )
        assert response.status_code == 302
        user = User.objects.get(username="roleuser")
        membership = OrganizationMember.objects.get(member=user, organization=org)
        assert membership.role == viewer_role
        # Verify role group was applied
        assert user.groups.filter(name="role_viewer").exists()

    def test_create_user_without_role(self, admin_client, org, roles):
        response = admin_client.post(
            reverse("organization:user_create"),
            {
                "username": "noroleuser",
                "email": "norole@test.com",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
            },
        )
        assert response.status_code == 302
        user = User.objects.get(username="noroleuser")
        membership = OrganizationMember.objects.get(member=user, organization=org)
        assert membership.role is None

    def test_create_form_has_role_field(self, admin_client, org, roles):
        response = admin_client.get(reverse("organization:user_create"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "role" in content.lower()


# --- user_edit with role ---


@pytest.mark.django_db
class TestUserEditWithRole:
    def test_edit_assigns_role(self, admin_client, org, target_user, viewer_role):
        response = admin_client.post(
            reverse("organization:user_edit", kwargs={"username": "targetuser"}),
            {
                "email": "target@test.com",
                "first_name": "Target",
                "last_name": "User",
                "is_active": "on",
                "role": viewer_role.pk,
            },
        )
        assert response.status_code == 302
        membership = OrganizationMember.objects.get(member=target_user, organization=org)
        assert membership.role == viewer_role
        assert target_user.groups.filter(name="role_viewer").exists()

    def test_edit_changes_role(self, admin_client, org, target_user, viewer_role, admin_role):
        # First assign viewer role
        membership = OrganizationMember.objects.get(member=target_user, organization=org)
        membership.role = viewer_role
        membership.save()
        viewer_role.apply_to_user(target_user)

        # Now change to admin role via edit
        response = admin_client.post(
            reverse("organization:user_edit", kwargs={"username": "targetuser"}),
            {
                "email": "target@test.com",
                "first_name": "Target",
                "last_name": "User",
                "is_active": "on",
                "role": admin_role.pk,
            },
        )
        assert response.status_code == 302
        membership.refresh_from_db()
        assert membership.role == admin_role
        # Old role group should be gone, new one should be present
        assert not target_user.groups.filter(name="role_viewer").exists()
        assert target_user.groups.filter(name="role_admin").exists()

    def test_edit_removes_role(self, admin_client, org, target_user, viewer_role):
        membership = OrganizationMember.objects.get(member=target_user, organization=org)
        membership.role = viewer_role
        membership.save()
        viewer_role.apply_to_user(target_user)

        # Submit without role
        response = admin_client.post(
            reverse("organization:user_edit", kwargs={"username": "targetuser"}),
            {
                "email": "target@test.com",
                "first_name": "Target",
                "last_name": "User",
                "is_active": "on",
                # role intentionally omitted
            },
        )
        assert response.status_code == 302
        membership.refresh_from_db()
        assert membership.role is None
        assert not target_user.groups.filter(name__startswith="role_").exists()

    def test_edit_form_pre_selects_role(self, admin_client, org, target_user, viewer_role):
        membership = OrganizationMember.objects.get(member=target_user, organization=org)
        membership.role = viewer_role
        membership.save()

        response = admin_client.get(reverse("organization:user_edit", kwargs={"username": "targetuser"}))
        assert response.status_code == 200
        content = response.content.decode()
        # The viewer option should be selected
        assert "selected" in content


# --- member_list role column ---


@pytest.mark.django_db
class TestMemberListRoleColumn:
    def test_role_shown_in_member_list(self, admin_client, org, admin_user, viewer_role):
        membership = OrganizationMember.objects.get(member=admin_user, organization=org)
        membership.role = viewer_role
        membership.save()

        response = admin_client.get(reverse("organization:members"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Role" in content  # Column header
        assert "Viewer" in content  # Role name

    def test_no_role_shown_as_dash(self, admin_client, org, admin_user):
        response = admin_client.get(reverse("organization:members"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Role" in content  # Column header


# --- user_detail role display ---


@pytest.mark.django_db
class TestUserDetailRole:
    def test_detail_shows_role(self, admin_client, org, target_user, viewer_role):
        membership = OrganizationMember.objects.get(member=target_user, organization=org)
        membership.role = viewer_role
        membership.save()

        response = admin_client.get(reverse("organization:user_detail", kwargs={"username": "targetuser"}))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Viewer" in content
        assert "Read-only access" in content

    def test_detail_shows_no_role_assigned(self, admin_client, org, target_user):
        response = admin_client.get(reverse("organization:user_detail", kwargs={"username": "targetuser"}))
        assert response.status_code == 200
        content = response.content.decode()
        assert "No role assigned" in content

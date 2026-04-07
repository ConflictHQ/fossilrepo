from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import models
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from core.pagination import PER_PAGE_OPTIONS, get_per_page
from core.permissions import P

from .forms import (
    MemberAddForm,
    OrganizationSettingsForm,
    OrgRoleForm,
    TeamForm,
    TeamMemberAddForm,
    UserCreateForm,
    UserEditForm,
    UserPasswordForm,
)
from .models import Organization, OrganizationMember, OrgRole, Team


def get_org():
    return Organization.objects.first()


# --- Organization Settings ---


@login_required
def org_settings(request):
    P.ORGANIZATION_VIEW.check(request.user)
    org = get_org()
    return render(request, "organization/settings.html", {"org": org})


@login_required
def org_settings_edit(request):
    P.ORGANIZATION_CHANGE.check(request.user)
    org = get_org()

    if request.method == "POST":
        form = OrganizationSettingsForm(request.POST, instance=org)
        if form.is_valid():
            org = form.save(commit=False)
            org.updated_by = request.user
            org.save()
            messages.success(request, "Organization settings updated.")
            return redirect("organization:settings")
    else:
        form = OrganizationSettingsForm(instance=org)

    return render(request, "organization/settings_form.html", {"form": form, "org": org})


# --- Members ---


@login_required
def member_list(request):
    P.ORGANIZATION_MEMBER_VIEW.check(request.user)
    org = get_org()
    members = OrganizationMember.objects.filter(organization=org).select_related("member", "role")

    search = request.GET.get("search", "").strip()
    if search:
        members = members.filter(member__username__icontains=search)

    per_page = get_per_page(request)
    paginator = Paginator(members, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    ctx = {
        "members": page_obj,
        "page_obj": page_obj,
        "org": org,
        "search": search,
        "per_page": per_page,
        "per_page_options": PER_PAGE_OPTIONS,
    }

    if request.headers.get("HX-Request"):
        return render(request, "organization/partials/member_table.html", ctx)

    return render(request, "organization/member_list.html", ctx)


@login_required
def member_add(request):
    P.ORGANIZATION_MEMBER_ADD.check(request.user)
    org = get_org()

    if request.method == "POST":
        form = MemberAddForm(request.POST, org=org)
        if form.is_valid():
            user = form.cleaned_data["user"]
            OrganizationMember.objects.create(member=user, organization=org, created_by=request.user)
            messages.success(request, f'Member "{user.username}" added.')
            return redirect("organization:members")
    else:
        form = MemberAddForm(org=org)

    return render(request, "organization/member_add.html", {"form": form, "org": org})


@login_required
def member_remove(request, username):
    P.ORGANIZATION_MEMBER_DELETE.check(request.user)
    org = get_org()
    membership = get_object_or_404(OrganizationMember, member__username=username, organization=org, deleted_at__isnull=True)

    if request.method == "POST":
        membership.soft_delete(user=request.user)
        messages.success(request, f'Member "{username}" removed.')

        if request.headers.get("HX-Request"):
            return HttpResponse(status=200, headers={"HX-Redirect": "/settings/members/"})

        return redirect("organization:members")

    return render(request, "organization/member_confirm_remove.html", {"membership": membership, "org": org})


# --- Teams ---


@login_required
def team_list(request):
    P.TEAM_VIEW.check(request.user)
    org = get_org()
    teams = Team.objects.filter(organization=org)

    search = request.GET.get("search", "").strip()
    if search:
        teams = teams.filter(name__icontains=search)

    per_page = get_per_page(request)
    paginator = Paginator(teams, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    ctx = {"teams": page_obj, "page_obj": page_obj, "search": search, "per_page": per_page, "per_page_options": PER_PAGE_OPTIONS}

    if request.headers.get("HX-Request"):
        return render(request, "organization/partials/team_table.html", ctx)

    return render(request, "organization/team_list.html", ctx)


@login_required
def team_create(request):
    P.TEAM_ADD.check(request.user)
    org = get_org()

    if request.method == "POST":
        form = TeamForm(request.POST)
        if form.is_valid():
            team = form.save(commit=False)
            team.organization = org
            team.created_by = request.user
            team.save()
            messages.success(request, f'Team "{team.name}" created.')
            return redirect("organization:team_detail", slug=team.slug)
    else:
        form = TeamForm()

    return render(request, "organization/team_form.html", {"form": form, "title": "New Team"})


@login_required
def team_detail(request, slug):
    P.TEAM_VIEW.check(request.user)
    team = get_object_or_404(Team, slug=slug, deleted_at__isnull=True)
    team_members = team.members.filter(is_active=True)
    return render(request, "organization/team_detail.html", {"team": team, "team_members": team_members})


@login_required
def team_update(request, slug):
    P.TEAM_CHANGE.check(request.user)
    team = get_object_or_404(Team, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        form = TeamForm(request.POST, instance=team)
        if form.is_valid():
            team = form.save(commit=False)
            team.updated_by = request.user
            team.save()
            messages.success(request, f'Team "{team.name}" updated.')
            return redirect("organization:team_detail", slug=team.slug)
    else:
        form = TeamForm(instance=team)

    return render(request, "organization/team_form.html", {"form": form, "team": team, "title": "Edit Team"})


@login_required
def team_delete(request, slug):
    P.TEAM_DELETE.check(request.user)
    team = get_object_or_404(Team, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        team.soft_delete(user=request.user)
        messages.success(request, f'Team "{team.name}" deleted.')

        if request.headers.get("HX-Request"):
            return HttpResponse(status=200, headers={"HX-Redirect": "/settings/teams/"})

        return redirect("organization:team_list")

    return render(request, "organization/team_confirm_delete.html", {"team": team})


@login_required
def team_member_add(request, slug):
    P.TEAM_CHANGE.check(request.user)
    team = get_object_or_404(Team, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        form = TeamMemberAddForm(request.POST, team=team)
        if form.is_valid():
            user = form.cleaned_data["user"]
            team.members.add(user)
            messages.success(request, f'"{user.username}" added to {team.name}.')
            return redirect("organization:team_detail", slug=team.slug)
    else:
        form = TeamMemberAddForm(team=team)

    return render(request, "organization/team_member_add.html", {"form": form, "team": team})


@login_required
def team_member_remove(request, slug, username):
    P.TEAM_CHANGE.check(request.user)
    team = get_object_or_404(Team, slug=slug, deleted_at__isnull=True)
    user = get_object_or_404(User, username=username)

    if request.method == "POST":
        team.members.remove(user)
        messages.success(request, f'"{username}" removed from {team.name}.')

        if request.headers.get("HX-Request"):
            return HttpResponse(status=200, headers={"HX-Redirect": f"/settings/teams/{team.slug}/"})

        return redirect("organization:team_detail", slug=team.slug)

    return render(request, "organization/team_member_confirm_remove.html", {"team": team, "member_user": user})


# --- User Management ---


def _check_user_management_permission(request):
    """User management requires superuser or ORGANIZATION_CHANGE permission."""
    if request.user.is_superuser:
        return True
    return P.ORGANIZATION_CHANGE.check(request.user)


@login_required
def user_create(request):
    _check_user_management_permission(request)
    org = get_org()

    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            role = form.cleaned_data.get("role")
            OrganizationMember.objects.create(member=user, organization=org, role=role, created_by=request.user)
            if role:
                role.apply_to_user(user)
            messages.success(request, f'User "{user.username}" created and added as member.')
            return redirect("organization:members")
    else:
        form = UserCreateForm()

    return render(request, "organization/user_form.html", {"form": form, "title": "New User"})


@login_required
def user_detail(request, username):
    P.ORGANIZATION_MEMBER_VIEW.check(request.user)
    org = get_org()
    target_user = get_object_or_404(User, username=username)
    membership = (
        OrganizationMember.objects.filter(member=target_user, organization=org, deleted_at__isnull=True).select_related("role").first()
    )
    user_teams = Team.objects.filter(members=target_user, organization=org, deleted_at__isnull=True)

    from fossil.user_keys import UserSSHKey

    ssh_keys = UserSSHKey.objects.filter(user=target_user)

    can_manage = request.user.is_superuser or P.ORGANIZATION_CHANGE.check(request.user, raise_error=False)

    return render(
        request,
        "organization/user_detail.html",
        {
            "target_user": target_user,
            "membership": membership,
            "user_teams": user_teams,
            "ssh_keys": ssh_keys,
            "can_manage": can_manage,
            "org": org,
        },
    )


@login_required
def user_edit(request, username):
    _check_user_management_permission(request)
    org = get_org()
    target_user = get_object_or_404(User, username=username)
    editing_self = request.user.pk == target_user.pk
    membership = (
        OrganizationMember.objects.filter(member=target_user, organization=org, deleted_at__isnull=True).select_related("role").first()
    )

    if request.method == "POST":
        form = UserEditForm(request.POST, instance=target_user, editing_self=editing_self)
        if form.is_valid():
            form.save()
            role = form.cleaned_data.get("role")
            if membership:
                membership.role = role
                membership.updated_by = request.user
                membership.save()
            if role:
                role.apply_to_user(target_user)
            else:
                OrgRole.remove_role_groups(target_user)
            messages.success(request, f'User "{target_user.username}" updated.')
            return redirect("organization:members")
    else:
        initial = {}
        if membership and membership.role:
            initial["role"] = membership.role.pk
        form = UserEditForm(instance=target_user, editing_self=editing_self, initial=initial)

    return render(
        request,
        "organization/user_form.html",
        {"form": form, "title": f"Edit {target_user.username}", "edit_user": target_user},
    )


@login_required
def user_password(request, username):
    target_user = get_object_or_404(User, username=username)
    editing_own = request.user.pk == target_user.pk

    # Allow changing own password, or require admin/org-change for others
    if not editing_own:
        _check_user_management_permission(request)

    if request.method == "POST":
        form = UserPasswordForm(request.POST)
        if form.is_valid():
            target_user.set_password(form.cleaned_data["new_password1"])
            target_user.save()
            messages.success(request, f'Password changed for "{target_user.username}".')
            return redirect("organization:user_detail", username=target_user.username)
    else:
        form = UserPasswordForm()

    return render(request, "organization/user_password.html", {"form": form, "target_user": target_user})


# --- Roles ---


@login_required
def role_list(request):
    P.ORGANIZATION_VIEW.check(request.user)
    roles = OrgRole.objects.annotate(
        member_count=models.Count("members", filter=models.Q(members__deleted_at__isnull=True)),
        permission_count=models.Count("permissions"),
    )
    return render(request, "organization/role_list.html", {"roles": roles})


@login_required
def role_detail(request, slug):
    P.ORGANIZATION_VIEW.check(request.user)
    role = get_object_or_404(OrgRole, slug=slug, deleted_at__isnull=True)
    role_permissions = role.permissions.select_related("content_type").order_by("content_type__app_label", "codename")

    # Group permissions by app label
    grouped = {}
    app_labels = {
        "organization": "Organization",
        "projects": "Projects",
        "pages": "Pages",
        "fossil": "Fossil",
    }
    for perm in role_permissions:
        app = perm.content_type.app_label
        label = app_labels.get(app, app.title())
        grouped.setdefault(label, []).append(perm)

    role_members = OrganizationMember.objects.filter(role=role, deleted_at__isnull=True).select_related("member")

    return render(
        request,
        "organization/role_detail.html",
        {"role": role, "grouped_permissions": grouped, "role_members": role_members},
    )


@login_required
def role_create(request):
    P.ORGANIZATION_CHANGE.check(request.user)

    if request.method == "POST":
        form = OrgRoleForm(request.POST)
        if form.is_valid():
            role = form.save(commit=False)
            role.created_by = request.user
            role.save()
            form.save_m2m()
            role.permissions.set(form.cleaned_data["permissions"])
            messages.success(request, f'Role "{role.name}" created.')
            return redirect("organization:role_detail", slug=role.slug)
    else:
        form = OrgRoleForm()

    return render(request, "organization/role_form.html", {"form": form, "title": "New Role"})


@login_required
def role_edit(request, slug):
    P.ORGANIZATION_CHANGE.check(request.user)
    role = get_object_or_404(OrgRole, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        form = OrgRoleForm(request.POST, instance=role)
        if form.is_valid():
            role = form.save(commit=False)
            role.updated_by = request.user
            role.save()
            role.permissions.set(form.cleaned_data["permissions"])
            messages.success(request, f'Role "{role.name}" updated.')
            return redirect("organization:role_detail", slug=role.slug)
    else:
        form = OrgRoleForm(instance=role)

    return render(request, "organization/role_form.html", {"form": form, "role": role, "title": f"Edit {role.name}"})


@login_required
def role_delete(request, slug):
    P.ORGANIZATION_CHANGE.check(request.user)
    role = get_object_or_404(OrgRole, slug=slug, deleted_at__isnull=True)
    active_members = OrganizationMember.objects.filter(role=role, deleted_at__isnull=True)

    if request.method == "POST":
        if active_members.exists():
            messages.error(
                request, f'Cannot delete role "{role.name}" -- it has {active_members.count()} active member(s). Reassign them first.'
            )
            return redirect("organization:role_detail", slug=role.slug)

        role.soft_delete(user=request.user)
        messages.success(request, f'Role "{role.name}" deleted.')

        if request.headers.get("HX-Request"):
            return HttpResponse(status=200, headers={"HX-Redirect": "/settings/roles/"})

        return redirect("organization:role_list")

    return render(
        request,
        "organization/role_confirm_delete.html",
        {"role": role, "active_members": active_members},
    )


@login_required
def audit_log(request):
    """Unified audit log across all tracked models. Requires superuser or org admin."""
    from core.pagination import manual_paginate

    if not request.user.is_superuser:
        P.ORGANIZATION_CHANGE.check(request.user)

    from fossil.models import FossilRepository
    from projects.models import Project

    trackable_models = [
        ("Project", Project),
        ("Organization", Organization),
        ("Team", Team),
        ("FossilRepository", FossilRepository),
    ]

    entries = []
    model_filter = request.GET.get("model", "").strip()

    for label, model in trackable_models:
        if model_filter and label.lower() != model_filter.lower():
            continue
        history_model = model.history.model
        qs = history_model.objects.all().select_related("history_user").order_by("-history_date")[:500]
        for h in qs:
            entries.append(
                {
                    "date": h.history_date,
                    "user": h.history_user,
                    "action": h.get_history_type_display(),
                    "model": label,
                    "object_repr": str(h),
                    "object_id": h.pk,
                }
            )

    entries.sort(key=lambda x: x["date"], reverse=True)

    per_page = get_per_page(request)
    entries, pagination = manual_paginate(entries, request, per_page=per_page)

    available_models = [label for label, _ in trackable_models]

    return render(
        request,
        "organization/audit_log.html",
        {
            "entries": entries,
            "model_filter": model_filter,
            "available_models": available_models,
            "pagination": pagination,
            "per_page": per_page,
            "per_page_options": PER_PAGE_OPTIONS,
        },
    )


@login_required
def role_initialize(request):
    P.ORGANIZATION_CHANGE.check(request.user)

    if request.method == "POST":
        from django.core.management import call_command

        call_command("seed_roles")
        messages.success(request, "Roles initialized successfully.")

    return redirect("organization:role_list")

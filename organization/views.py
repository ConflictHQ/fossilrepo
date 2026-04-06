from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from core.permissions import P

from .forms import MemberAddForm, OrganizationSettingsForm, TeamForm, TeamMemberAddForm
from .models import Organization, OrganizationMember, Team


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
    members = OrganizationMember.objects.filter(organization=org).select_related("member")

    search = request.GET.get("search", "").strip()
    if search:
        members = members.filter(member__username__icontains=search)

    if request.headers.get("HX-Request"):
        return render(request, "organization/partials/member_table.html", {"members": members, "org": org})

    return render(request, "organization/member_list.html", {"members": members, "org": org, "search": search})


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

    if request.headers.get("HX-Request"):
        return render(request, "organization/partials/team_table.html", {"teams": teams})

    return render(request, "organization/team_list.html", {"teams": teams, "search": search})


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

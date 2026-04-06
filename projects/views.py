from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from core.permissions import P
from organization.models import Team
from organization.views import get_org

from .forms import ProjectForm, ProjectTeamAddForm, ProjectTeamEditForm
from .models import Project, ProjectTeam


@login_required
def project_list(request):
    P.PROJECT_VIEW.check(request.user)
    projects = Project.objects.all()

    search = request.GET.get("search", "").strip()
    if search:
        projects = projects.filter(name__icontains=search)

    if request.headers.get("HX-Request"):
        return render(request, "projects/partials/project_table.html", {"projects": projects})

    return render(request, "projects/project_list.html", {"projects": projects, "search": search})


@login_required
def project_create(request):
    P.PROJECT_ADD.check(request.user)
    org = get_org()

    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.organization = org
            project.created_by = request.user
            project.save()
            messages.success(request, f'Project "{project.name}" created.')
            return redirect("projects:detail", slug=project.slug)
    else:
        form = ProjectForm()

    return render(request, "projects/project_form.html", {"form": form, "title": "New Project"})


@login_required
def project_detail(request, slug):
    P.PROJECT_VIEW.check(request.user)
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    project_teams = project.project_teams.filter(deleted_at__isnull=True).select_related("team")

    # Get Fossil repo stats if available
    repo_stats = None
    recent_commits = []
    commit_activity = []
    top_contributors = []
    try:
        from fossil.models import FossilRepository
        from fossil.reader import FossilReader

        fossil_repo = FossilRepository.objects.filter(project=project, deleted_at__isnull=True).first()
        if fossil_repo and fossil_repo.exists_on_disk:
            with FossilReader(fossil_repo.full_path) as reader:
                repo_stats = reader.get_metadata()
                recent_commits = reader.get_timeline(limit=5, event_type="ci")
                commit_activity = reader.get_commit_activity(weeks=52)
                top_contributors = reader.get_top_contributors(limit=8)
    except Exception:
        pass

    import json

    return render(
        request,
        "projects/project_detail.html",
        {
            "project": project,
            "project_teams": project_teams,
            "repo_stats": repo_stats,
            "recent_commits": recent_commits,
            "commit_activity_json": json.dumps([c["count"] for c in commit_activity]),
            "top_contributors": top_contributors,
        },
    )


@login_required
def project_update(request, slug):
    P.PROJECT_CHANGE.check(request.user)
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            project = form.save(commit=False)
            project.updated_by = request.user
            project.save()
            messages.success(request, f'Project "{project.name}" updated.')
            return redirect("projects:detail", slug=project.slug)
    else:
        form = ProjectForm(instance=project)

    return render(request, "projects/project_form.html", {"form": form, "project": project, "title": "Edit Project"})


@login_required
def project_delete(request, slug):
    P.PROJECT_DELETE.check(request.user)
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        project.soft_delete(user=request.user)
        messages.success(request, f'Project "{project.name}" deleted.')

        if request.headers.get("HX-Request"):
            return HttpResponse(status=200, headers={"HX-Redirect": "/projects/"})

        return redirect("projects:list")

    return render(request, "projects/project_confirm_delete.html", {"project": project})


# --- Team assignment ---


@login_required
def project_team_add(request, slug):
    P.PROJECT_CHANGE.check(request.user)
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        form = ProjectTeamAddForm(request.POST, project=project)
        if form.is_valid():
            team = form.cleaned_data["team"]
            role = form.cleaned_data["role"]
            ProjectTeam.objects.create(project=project, team=team, role=role, created_by=request.user)
            messages.success(request, f'Team "{team.name}" added with {role} access.')
            return redirect("projects:detail", slug=project.slug)
    else:
        form = ProjectTeamAddForm(project=project)

    return render(request, "projects/project_team_add.html", {"form": form, "project": project})


@login_required
def project_team_edit(request, slug, team_slug):
    P.PROJECT_CHANGE.check(request.user)
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    team = get_object_or_404(Team, slug=team_slug, deleted_at__isnull=True)
    project_team = get_object_or_404(ProjectTeam, project=project, team=team, deleted_at__isnull=True)

    if request.method == "POST":
        form = ProjectTeamEditForm(request.POST)
        if form.is_valid():
            project_team.role = form.cleaned_data["role"]
            project_team.updated_by = request.user
            project_team.save()
            messages.success(request, f'Team "{team.name}" role updated to {project_team.role}.')
            return redirect("projects:detail", slug=project.slug)
    else:
        form = ProjectTeamEditForm(initial={"role": project_team.role})

    return render(request, "projects/project_team_edit.html", {"form": form, "project": project, "team": team})


@login_required
def project_team_remove(request, slug, team_slug):
    P.PROJECT_CHANGE.check(request.user)
    project = get_object_or_404(Project, slug=slug, deleted_at__isnull=True)
    team = get_object_or_404(Team, slug=team_slug, deleted_at__isnull=True)
    project_team = get_object_or_404(ProjectTeam, project=project, team=team, deleted_at__isnull=True)

    if request.method == "POST":
        project_team.soft_delete(user=request.user)
        messages.success(request, f'Team "{team.name}" removed from project.')

        if request.headers.get("HX-Request"):
            return HttpResponse(status=200, headers={"HX-Redirect": f"/projects/{project.slug}/"})

        return redirect("projects:detail", slug=project.slug)

    return render(request, "projects/project_team_confirm_remove.html", {"project": project, "team": team})

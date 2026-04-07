from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from core.permissions import P
from organization.models import Team
from organization.views import get_org

from .forms import ProjectForm, ProjectGroupForm, ProjectTeamAddForm, ProjectTeamEditForm
from .models import Project, ProjectGroup, ProjectTeam


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

            # Handle repo source: clone from URL if requested
            repo_source = form.cleaned_data.get("repo_source", "empty")
            clone_url = form.cleaned_data.get("clone_url", "").strip()

            if repo_source == "fossil_url" and clone_url:
                _clone_fossil_repo(request, project, clone_url)

            messages.success(request, f'Project "{project.name}" created.')
            return redirect("projects:detail", slug=project.slug)
    else:
        form = ProjectForm()

    return render(request, "projects/project_form.html", {"form": form, "title": "New Project"})


def _clone_fossil_repo(request, project, clone_url):
    """Clone a Fossil repo from a remote URL, replacing the empty file created by the signal."""
    import subprocess

    from fossil.cli import FossilCLI
    from fossil.models import FossilRepository

    fossil_repo = FossilRepository.objects.filter(project=project).first()
    if not fossil_repo:
        return

    cli = FossilCLI()
    if not cli.is_available():
        messages.warning(request, "Fossil binary not available -- clone skipped.")
        return

    # Remove the empty file created by the signal so we can clone into that path
    if fossil_repo.full_path.exists():
        fossil_repo.full_path.unlink()

    try:
        result = subprocess.run(
            [cli.binary, "clone", clone_url, str(fossil_repo.full_path)],
            capture_output=True,
            text=True,
            timeout=120,
            env=cli._env,
        )
        if result.returncode == 0:
            fossil_repo.remote_url = clone_url
            fossil_repo.file_size_bytes = fossil_repo.full_path.stat().st_size if fossil_repo.exists_on_disk else 0
            fossil_repo.save(update_fields=["remote_url", "file_size_bytes", "updated_at", "version"])
            messages.success(request, f"Repository cloned from {clone_url}")
        else:
            messages.warning(request, f"Clone failed: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        messages.warning(request, "Clone timed out -- the repository may be large. Try pulling later.")


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

    # Check if user is watching this project
    is_watching = False
    if request.user.is_authenticated:
        from fossil.notifications import ProjectWatch

        is_watching = ProjectWatch.objects.filter(user=request.user, project=project, deleted_at__isnull=True).exists()

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
            "is_watching": is_watching,
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


# --- Project Groups ---


@login_required
def group_list(request):
    P.PROJECT_GROUP_VIEW.check(request.user)
    groups = ProjectGroup.objects.all().prefetch_related("projects")

    if request.headers.get("HX-Request"):
        return render(request, "projects/partials/group_table.html", {"groups": groups})

    return render(request, "projects/group_list.html", {"groups": groups})


@login_required
def group_create(request):
    P.PROJECT_GROUP_ADD.check(request.user)

    if request.method == "POST":
        form = ProjectGroupForm(request.POST)
        if form.is_valid():
            group = form.save(commit=False)
            group.created_by = request.user
            group.save()
            messages.success(request, f'Group "{group.name}" created.')
            return redirect("projects:group_detail", slug=group.slug)
    else:
        form = ProjectGroupForm()

    return render(request, "projects/group_form.html", {"form": form, "title": "New Group"})


@login_required
def group_detail(request, slug):
    P.PROJECT_GROUP_VIEW.check(request.user)
    group = get_object_or_404(ProjectGroup, slug=slug, deleted_at__isnull=True)
    group_projects = Project.objects.filter(group=group)

    return render(request, "projects/group_detail.html", {"group": group, "group_projects": group_projects})


@login_required
def group_edit(request, slug):
    P.PROJECT_GROUP_CHANGE.check(request.user)
    group = get_object_or_404(ProjectGroup, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        form = ProjectGroupForm(request.POST, instance=group)
        if form.is_valid():
            group = form.save(commit=False)
            group.updated_by = request.user
            group.save()
            messages.success(request, f'Group "{group.name}" updated.')
            return redirect("projects:group_detail", slug=group.slug)
    else:
        form = ProjectGroupForm(instance=group)

    return render(request, "projects/group_form.html", {"form": form, "group": group, "title": "Edit Group"})


@login_required
def group_delete(request, slug):
    P.PROJECT_GROUP_DELETE.check(request.user)
    group = get_object_or_404(ProjectGroup, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        # Unlink projects from this group before soft-deleting
        Project.objects.filter(group=group).update(group=None)
        group.soft_delete(user=request.user)
        messages.success(request, f'Group "{group.name}" deleted.')

        if request.headers.get("HX-Request"):
            return HttpResponse(status=200, headers={"HX-Redirect": "/projects/groups/"})

        return redirect("projects:group_list")

    return render(request, "projects/group_confirm_delete.html", {"group": group})

"""Project-level access control based on visibility and team roles.

Usage in views:
    from projects.access import can_read_project, can_write_project, require_project_read

    # Check and raise 403/redirect
    require_project_read(request, project)

    # Boolean check
    if can_write_project(request.user, project):
        ...
"""

from django.core.exceptions import PermissionDenied

from projects.models import Project, ProjectTeam


def get_user_role(user, project: Project) -> str | None:
    """Get the highest role a user has on a project via their teams.

    Returns "admin", "write", "read", or None.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return None

    if user.is_superuser:
        return "admin"

    # Check all teams the user belongs to that are assigned to this project
    user_team_ids = set(user.teams.values_list("id", flat=True))
    project_teams = ProjectTeam.objects.filter(project=project, team_id__in=user_team_ids, deleted_at__isnull=True).values_list(
        "role", flat=True
    )

    roles = set(project_teams)
    if "admin" in roles:
        return "admin"
    if "write" in roles:
        return "write"
    if "read" in roles:
        return "read"
    return None


def can_read_project(user, project: Project) -> bool:
    """Can this user read the project?

    - Public: anyone (even anonymous)
    - Internal: any authenticated user
    - Private: team members only (or superuser)
    """
    if project.visibility == "public":
        return True
    if project.visibility == "internal":
        return user and user.is_authenticated and user.is_active
    # Private
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return get_user_role(user, project) is not None


def can_write_project(user, project: Project) -> bool:
    """Can this user write to the project (create tickets, edit wiki, etc.)?"""
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    role = get_user_role(user, project)
    return role in ("write", "admin")


def can_admin_project(user, project: Project) -> bool:
    """Can this user administer the project (manage teams, settings, sync)?"""
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return get_user_role(user, project) == "admin"


def require_project_read(request, project: Project):
    """Raise PermissionDenied if user can't read the project."""
    if not can_read_project(request.user, project):
        if not request.user.is_authenticated:
            raise PermissionDenied("Authentication required.")
        raise PermissionDenied(f"You don't have access to {project.name}.")


def require_project_write(request, project: Project):
    """Raise PermissionDenied if user can't write to the project."""
    if not can_write_project(request.user, project):
        raise PermissionDenied(f"Write access required for {project.name}.")


def require_project_admin(request, project: Project):
    """Raise PermissionDenied if user can't admin the project."""
    if not can_admin_project(request.user, project):
        raise PermissionDenied(f"Admin access required for {project.name}.")

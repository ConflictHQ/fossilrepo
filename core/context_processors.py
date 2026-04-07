from pages.models import Page
from projects.models import Project, ProjectGroup


def sidebar(request):
    if not request.user.is_authenticated:
        return {}

    projects = Project.objects.all().select_related("group")
    pages = Page.objects.filter(is_published=True)
    if request.user.has_perm("pages.change_page") or request.user.is_superuser:
        pages = Page.objects.all()

    # Build grouped structure for sidebar
    groups = ProjectGroup.objects.filter(deleted_at__isnull=True)

    grouped_projects = []
    grouped_ids = set()
    for group in groups:
        group_projects = [p for p in projects if p.group_id == group.id]
        if group_projects:
            grouped_projects.append({"group": group, "projects": group_projects})
            grouped_ids.update(p.id for p in group_projects)

    ungrouped_projects = [p for p in projects if p.id not in grouped_ids]

    return {
        "sidebar_projects": projects,
        "sidebar_grouped": grouped_projects,
        "sidebar_ungrouped": ungrouped_projects,
        "sidebar_pages": pages,
    }

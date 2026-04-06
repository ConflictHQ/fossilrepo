from pages.models import Page
from projects.models import Project


def sidebar(request):
    if not request.user.is_authenticated:
        return {}

    projects = Project.objects.all()
    pages = Page.objects.filter(is_published=True)
    if request.user.has_perm("pages.change_page") or request.user.is_superuser:
        pages = Page.objects.all()

    return {
        "sidebar_projects": projects,
        "sidebar_pages": pages,
    }

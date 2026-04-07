"""Custom view decorators for fossilrepo."""

from functools import wraps

from django.contrib.auth.decorators import login_required


def public_or_login(view_func):
    """Allow anonymous access to public project views.

    For views that take a `slug` parameter: if the project is public,
    let anonymous users through. Otherwise, redirect to login.

    This replaces @login_required on read-only project/fossil views.
    The view itself must still call require_project_read() for the
    actual permission check.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            return view_func(request, *args, **kwargs)

        # Check if this is a public project
        slug = kwargs.get("slug") or (args[0] if args else None)
        if slug:
            from projects.models import Project

            try:
                project = Project.objects.get(slug=slug, deleted_at__isnull=True)
                if project.visibility == "public":
                    return view_func(request, *args, **kwargs)
            except Project.DoesNotExist:
                pass

        # Not public or no slug -- require login
        return login_required(view_func)(request, *args, **kwargs)

    return wrapper

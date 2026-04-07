import markdown
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe

from core.pagination import PER_PAGE_OPTIONS, get_per_page
from core.permissions import P
from core.sanitize import sanitize_html
from organization.views import get_org

from .forms import PageForm
from .models import Page


@login_required
def page_list(request):
    P.PAGE_VIEW.check(request.user)
    pages = Page.objects.filter(is_published=True)

    if request.user.has_perm("pages.change_page") or request.user.is_superuser:
        pages = Page.objects.all()

    search = request.GET.get("search", "").strip()
    if search:
        pages = pages.filter(name__icontains=search)

    per_page = get_per_page(request)
    paginator = Paginator(pages, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    ctx = {"pages": page_obj, "page_obj": page_obj, "search": search, "per_page": per_page, "per_page_options": PER_PAGE_OPTIONS}

    if request.headers.get("HX-Request"):
        return render(request, "pages/partials/page_table.html", ctx)

    return render(request, "pages/page_list.html", ctx)


@login_required
def page_create(request):
    P.PAGE_ADD.check(request.user)
    org = get_org()

    if request.method == "POST":
        form = PageForm(request.POST)
        if form.is_valid():
            page = form.save(commit=False)
            page.organization = org
            page.created_by = request.user
            page.save()
            messages.success(request, f'Page "{page.name}" created.')
            return redirect("pages:detail", slug=page.slug)
    else:
        form = PageForm()

    return render(request, "pages/page_form.html", {"form": form, "title": "New Page"})


@login_required
def page_detail(request, slug):
    P.PAGE_VIEW.check(request.user)
    page = get_object_or_404(Page, slug=slug, deleted_at__isnull=True)
    content_html = mark_safe(sanitize_html(markdown.markdown(page.content, extensions=["fenced_code", "tables", "toc"])))
    return render(request, "pages/page_detail.html", {"page": page, "content_html": content_html})


@login_required
def page_update(request, slug):
    P.PAGE_CHANGE.check(request.user)
    page = get_object_or_404(Page, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        form = PageForm(request.POST, instance=page)
        if form.is_valid():
            page = form.save(commit=False)
            page.updated_by = request.user
            page.save()
            messages.success(request, f'Page "{page.name}" updated.')
            return redirect("pages:detail", slug=page.slug)
    else:
        form = PageForm(instance=page)

    return render(request, "pages/page_form.html", {"form": form, "page": page, "title": "Edit Page"})


@login_required
def page_delete(request, slug):
    P.PAGE_DELETE.check(request.user)
    page = get_object_or_404(Page, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        page.soft_delete(user=request.user)
        messages.success(request, f'Page "{page.name}" deleted.')

        if request.headers.get("HX-Request"):
            return HttpResponse(status=200, headers={"HX-Redirect": "/kb/"})

        return redirect("pages:list")

    return render(request, "pages/page_confirm_delete.html", {"page": page})

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from core.permissions import P

from .forms import ItemForm
from .models import Item


@login_required
def item_list(request):
    P.ITEM_VIEW.check(request.user)
    items = Item.objects.all()

    search = request.GET.get("search", "").strip()
    if search:
        items = items.filter(name__icontains=search)

    if request.headers.get("HX-Request"):
        return render(request, "items/partials/item_table.html", {"items": items})

    return render(request, "items/item_list.html", {"items": items, "search": search})


@login_required
def item_create(request):
    P.ITEM_ADD.check(request.user)

    if request.method == "POST":
        form = ItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.created_by = request.user
            item.save()
            messages.success(request, f'Item "{item.name}" created.')
            return redirect("items:detail", slug=item.slug)
    else:
        form = ItemForm()

    return render(request, "items/item_form.html", {"form": form, "title": "New Item"})


@login_required
def item_detail(request, slug):
    P.ITEM_VIEW.check(request.user)
    item = get_object_or_404(Item, slug=slug, deleted_at__isnull=True)
    return render(request, "items/item_detail.html", {"item": item})


@login_required
def item_update(request, slug):
    P.ITEM_CHANGE.check(request.user)
    item = get_object_or_404(Item, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        form = ItemForm(request.POST, instance=item)
        if form.is_valid():
            item = form.save(commit=False)
            item.updated_by = request.user
            item.save()
            messages.success(request, f'Item "{item.name}" updated.')
            return redirect("items:detail", slug=item.slug)
    else:
        form = ItemForm(instance=item)

    return render(request, "items/item_form.html", {"form": form, "item": item, "title": "Edit Item"})


@login_required
def item_delete(request, slug):
    P.ITEM_DELETE.check(request.user)
    item = get_object_or_404(Item, slug=slug, deleted_at__isnull=True)

    if request.method == "POST":
        item.soft_delete(user=request.user)
        messages.success(request, f'Item "{item.name}" deleted.')

        if request.headers.get("HX-Request"):
            from django.http import HttpResponse

            return HttpResponse(status=200, headers={"HX-Redirect": "/items/"})

        return redirect("items:list")

    return render(request, "items/item_confirm_delete.html", {"item": item})

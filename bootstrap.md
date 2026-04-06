# Fossilrepo Django + HTMX -- Bootstrap

This is the primary conventions document. All agent shims (`CLAUDE.md`, `AGENTS.md`) point here.

An agent given this document and a business requirement should be able to generate correct, idiomatic code without exploring the codebase.

---

## What's Already Built

| Layer | What's there |
|---|---|
| Auth | Session-based auth (auth1), login/logout views with templates, rate limiting |
| Data | Postgres 16, `Tracking` base model (version, created/updated/deleted by+at, soft deletes, history) |
| API | Django views returning HTML (full pages + HTMX partials) |
| Permissions | Group-based via `P` enum, checked in every view |
| Async | Celery worker + beat, Redis broker |
| Admin | Django Admin with `BaseCoreAdmin` (import/export, tracking fields) |
| Infra | Docker Compose: postgres, redis, celery-worker, celery-beat, mailpit |
| CI | GitHub Actions: lint (Ruff) + tests (Postgres + Redis services) |
| Seed | `python manage.py seed` creates admin/viewer users, sample items |
| Frontend | HTMX 2.0 + Alpine.js 3 + Tailwind CSS, server-rendered templates |

---

## App Structure

| App | Purpose |
|---|---|
| `config` | Django settings, URLs, Celery configuration |
| `core` | Base models (Tracking, BaseCoreModel), admin (BaseCoreAdmin), permissions (P enum), middleware |
| `auth1` | Session-based authentication: login/logout views with rate limiting |
| `organization` | Organization + OrganizationMember models |
| `items` | Example CRUD domain demonstrating all patterns |
| `testdata` | `seed` management command for development data |

---

## Conventions

### Models

All business models inherit from one of:

**`Tracking`** (abstract) — audit trails:
```python
from core.models import Tracking

class Invoice(Tracking):
    amount = models.DecimalField(...)
```
Provides: `version` (auto-increments), `created_at/by`, `updated_at/by`, `deleted_at/by`, `history` (simple_history).

**`BaseCoreModel(Tracking)`** (abstract) — named entities:
```python
from core.models import BaseCoreModel

class Item(BaseCoreModel):
    price = models.DecimalField(...)
```
Adds: `guid` (UUID), `name`, `slug` (auto-generated, unique), `description`.

**Soft deletes:** call `obj.soft_delete(user=request.user)`, never `.delete()`.

**ActiveManager:** Use `objects` (excludes deleted) for queries, `all_objects` for admin.

---

### Views (HTMX Pattern)

Views return full pages for normal requests, HTMX partials for `HX-Request`:

```python
@login_required
def item_list(request):
    P.ITEM_VIEW.check(request.user)
    items = Item.objects.all()

    if request.headers.get("HX-Request"):
        return render(request, "items/partials/item_table.html", {"items": items})

    return render(request, "items/item_list.html", {"items": items})
```

**URL patterns** follow CRUD convention:
```python
urlpatterns = [
    path("", views.item_list, name="list"),
    path("create/", views.item_create, name="create"),
    path("<slug:slug>/", views.item_detail, name="detail"),
    path("<slug:slug>/edit/", views.item_update, name="update"),
    path("<slug:slug>/delete/", views.item_delete, name="delete"),
]
```

---

### Permissions

Group-based. Never user-based. Checked in every view.

```python
from core.permissions import P

P.ITEM_VIEW.check(request.user)          # raises PermissionDenied if denied
P.ITEM_ADD.check(request.user, raise_error=False)  # returns False instead
```

Template guards:
```html
{% if perms.items.view_item %}
  <a href="{% url 'items:list' %}">Items</a>
{% endif %}
```

---

### Admin

All admin classes inherit `BaseCoreAdmin`:
```python
from core.admin import BaseCoreAdmin

@admin.register(Item)
class ItemAdmin(BaseCoreAdmin):
    list_display = ("name", "slug", "price", "created_at")
    search_fields = ("name", "slug")
```

`BaseCoreAdmin` provides: audit fields as readonly, `created_by`/`updated_by` auto-set, import/export.

---

### Templates

- `base.html` — layout with HTMX, Alpine.js, Tailwind CSS, CSRF injection, messages
- `includes/nav.html` — navigation bar with permission guards
- `{app}/partials/*.html` — HTMX partial templates (no `{% extends %}`)
- CSRF token sent with all HTMX requests via `htmx:configRequest` event

Alpine.js patterns for client-side interactivity:
```html
<div x-data="{ open: false }">
  <button @click="open = !open">Toggle</button>
  <div x-show="open" x-transition>Content</div>
</div>
```

---

### Tests

pytest + real Postgres. Assert against database state.

```python
@pytest.mark.django_db
class TestItemCreate:
    def test_create_saves_item(self, admin_client, admin_user):
        response = admin_client.post(reverse("items:create"), {
            "name": "Widget", "price": "9.99", ...
        })
        assert response.status_code == 302
        item = Item.objects.get(name="Widget")
        assert item.created_by == admin_user

    def test_create_denied_for_viewer(self, viewer_client):
        response = viewer_client.get(reverse("items:create"))
        assert response.status_code == 403
```

Both allowed AND denied permission cases for every endpoint.

---

### Code Style

| Tool | Config |
|------|--------|
| Ruff (lint + format) | `pyproject.toml`, line length 140 |
| Import sorting | Ruff isort rules |
| Python version | 3.12+ |

Run `ruff check .` and `ruff format --check .` before committing.

---

## Adding a New App

```bash
# 1. Create the app
python manage.py startapp myapp

# 2. Add to INSTALLED_APPS in config/settings.py

# 3. Create models inheriting Tracking or BaseCoreModel

# 4. Create migrations
python manage.py makemigrations

# 5. Create admin (inherit BaseCoreAdmin)

# 6. Create views with @login_required + P.PERMISSION.check()

# 7. Create URL patterns (list, detail, create, update, delete)

# 8. Create templates (full page + HTMX partials)

# 9. Add permission entries to core/permissions.py P enum

# 10. Write tests (allowed + denied)
python -m pytest --cov -v
```

---

## Ports (local Docker)

| Service | URL |
|---|---|
| Django | http://localhost:8000 |
| Django Admin | http://localhost:8000/admin/ |
| Health | http://localhost:8000/health/ |
| Mailpit | http://localhost:8025 |
| Postgres | localhost:5432 |
| Redis | localhost:6379 |

---

## Common Commands

```bash
make up              # Start the stack
make build           # Build and start
make down            # Stop the stack
make migrate         # Run migrations
make migrations      # Create migrations
make seed            # Load dev fixtures
make test            # Run tests with coverage
make lint            # Run Ruff check + format
make superuser       # Create Django superuser
make shell           # Shell into container
make logs            # Tail Django logs
```

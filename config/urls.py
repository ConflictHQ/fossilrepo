import time
from datetime import UTC, datetime

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect as _redirect
from django.urls import include, path
from django.views.generic import RedirectView


def _oauth_github_callback(request):
    """Global GitHub OAuth callback. Extracts slug from state param and delegates."""
    from django.contrib import messages

    state = request.GET.get("state", "")
    parts = state.split(":")
    if len(parts) < 3:
        return _redirect("/dashboard/")

    slug = parts[0]
    nonce = parts[2]

    expected_nonce = request.session.pop("oauth_state_nonce", "")
    if not nonce or nonce != expected_nonce:
        messages.error(request, "OAuth state mismatch. Please try again.")
        return _redirect(f"/projects/{slug}/fossil/sync/git/")

    from fossil.oauth import github_exchange_token

    result = github_exchange_token(request, slug)
    if result.get("token"):
        request.session["github_oauth_token"] = result["token"]
        request.session["github_oauth_user"] = result.get("username", "")
    return _redirect(f"/projects/{slug}/fossil/sync/git/")


def _oauth_gitlab_callback(request):
    """Global GitLab OAuth callback. Extracts slug from state param and delegates."""
    from django.contrib import messages

    state = request.GET.get("state", "")
    parts = state.split(":")
    if len(parts) < 3:
        return _redirect("/dashboard/")

    slug = parts[0]
    nonce = parts[2]

    expected_nonce = request.session.pop("oauth_state_nonce", "")
    if not nonce or nonce != expected_nonce:
        messages.error(request, "OAuth state mismatch. Please try again.")
        return _redirect(f"/projects/{slug}/fossil/sync/git/")

    from fossil.oauth import gitlab_exchange_token

    result = gitlab_exchange_token(request, slug)
    if result.get("token"):
        request.session["gitlab_oauth_token"] = result["token"]
    return _redirect(f"/projects/{slug}/fossil/sync/git/")


admin.site.site_header = settings.ADMIN_SITE_HEADER
admin.site.site_title = settings.ADMIN_SITE_TITLE
admin.site.index_title = "Welcome to Fossilrepo"

_START_TIME = time.monotonic()


def _uptime_str():
    secs = int(time.monotonic() - _START_TIME)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def health_check(request):
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception as e:
        return JsonResponse(
            {
                "service": "fossilrepo-django-htmx",
                "version": settings.VERSION,
                "status": "error",
                "uptime": _uptime_str(),
                "timestamp": datetime.now(UTC).isoformat(),
                "checks": {"database": "error", "detail": str(e)},
            },
            status=503,
        )

    return JsonResponse(
        {
            "service": "fossilrepo-django-htmx",
            "version": settings.VERSION,
            "status": "ok",
            "uptime": _uptime_str(),
            "timestamp": datetime.now(UTC).isoformat(),
            "checks": {"database": "ok" if db_ok else "error"},
            "links": {
                "app": "/dashboard/",
                "admin": "/admin/",
                "status": "/status/",
                "login": "/auth/login/",
            },
        }
    )


def status_page(request):
    version = settings.VERSION
    env = getattr(settings, "DJANGO_CONFIGURATION", "Local")
    uptime = _uptime_str()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    env_color = {
        "Local": "#22c55e",
        "Staging": "#f59e0b",
        "Production": "#ef4444",
    }.get(env, "#6b7280")

    links = [
        ("App", "/dashboard/", "Django + HTMX application"),
        ("Admin", "/admin/", "Django admin — users, permissions, data"),
        ("Health", "/health/", "Service health checks (JSON)"),
        ("Login", "/auth/login/", "Session-based authentication"),
    ]

    links_html = "\n".join(
        f"""<a href="{url}" class="link-card">
              <span class="link-title">{name}</span>
              <span class="link-desc">{desc}</span>
              <span class="link-arrow">&rarr;</span>
            </a>"""
        for name, url, desc in links
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Fossilrepo Status</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg: #0a0a0a; --surface: #111111; --border: #1f1f1f;
      --text: #e5e5e5; --muted: #6b7280; --accent: #ffffff; --green: #22c55e;
    }}
    body {{
      background: var(--bg); color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh; display: flex; flex-direction: column;
      align-items: center; justify-content: center; padding: 2rem;
    }}
    .container {{ width: 100%; max-width: 560px; display: flex; flex-direction: column; gap: 2rem; }}
    .header {{ display: flex; flex-direction: column; gap: 0.5rem; }}
    .wordmark {{ font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; color: var(--accent); }}
    .tagline {{ font-size: 0.875rem; color: var(--muted); }}
    .status-bar {{
      background: var(--surface); border: 1px solid var(--border); border-radius: 0.75rem;
      padding: 1rem 1.25rem; display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
    }}
    .status-dot {{
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--green); box-shadow: 0 0 6px var(--green); flex-shrink: 0;
    }}
    .status-text {{ font-size: 0.875rem; font-weight: 500; flex: 1; }}
    .meta-pills {{ display: flex; gap: 0.5rem; flex-wrap: wrap; }}
    .pill {{
      font-size: 0.75rem; padding: 0.2rem 0.6rem; border-radius: 999px;
      border: 1px solid var(--border); color: var(--muted); white-space: nowrap;
    }}
    .pill-env {{ border-color: {env_color}33; color: {env_color}; }}
    .links {{ display: flex; flex-direction: column; gap: 0.5rem; }}
    .links-label {{
      font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.08em; color: var(--muted);
      padding: 0 0.25rem; margin-bottom: 0.25rem;
    }}
    .link-card {{
      background: var(--surface); border: 1px solid var(--border); border-radius: 0.625rem;
      padding: 0.875rem 1.25rem; display: grid;
      grid-template-columns: 1fr auto; grid-template-rows: auto auto;
      gap: 0.125rem 0.5rem; text-decoration: none; color: inherit;
      transition: border-color 0.15s, background 0.15s;
    }}
    .link-card:hover {{ border-color: #2f2f2f; background: #161616; }}
    .link-title {{
      font-size: 0.875rem; font-weight: 500; color: var(--text);
      grid-column: 1; grid-row: 1;
    }}
    .link-desc {{ font-size: 0.75rem; color: var(--muted); grid-column: 1; grid-row: 2; }}
    .link-arrow {{
      font-size: 1rem; color: var(--muted); grid-column: 2; grid-row: 1 / 3;
      align-self: center; transition: color 0.15s, transform 0.15s;
    }}
    .link-card:hover .link-arrow {{ color: var(--text); transform: translateX(2px); }}
    .footer {{ font-size: 0.7rem; color: var(--muted); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.25rem; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="wordmark">Fossilrepo</div>
      <div class="tagline">Server-rendered Django + HTMX.</div>
    </div>
    <div class="status-bar">
      <div class="status-dot"></div>
      <div class="status-text">All systems operational</div>
      <div class="meta-pills">
        <span class="pill pill-env">{env}</span>
        <span class="pill">v{version}</span>
        <span class="pill">&uarr; {uptime}</span>
      </div>
    </div>
    <div class="links">
      <div class="links-label">Endpoints</div>
      {links_html}
    </div>
    <div class="footer">
      <span>fossilrepo-django-htmx</span>
      <span>{now}</span>
    </div>
  </div>
</body>
</html>"""

    return HttpResponse(html)


def _explore_view(request):
    from projects.views import explore

    return explore(request)


urlpatterns = [
    path("", lambda request: _redirect("/explore/") if not request.user.is_authenticated else _redirect("/dashboard/"), name="home"),
    path("profile/", RedirectView.as_view(pattern_name="accounts:profile", permanent=False)),
    path("status/", status_page, name="status"),
    path("explore/", _explore_view, name="explore"),
    path("dashboard/", include("core.urls")),
    path("auth/", include("accounts.urls")),
    path("settings/", include("organization.urls")),
    path("projects/", include("projects.urls")),
    path("projects/<slug:slug>/fossil/", include("fossil.urls")),
    path("kb/", include("pages.urls")),
    path("oauth/callback/github/", _oauth_github_callback, name="oauth_github_callback_global"),
    path("oauth/callback/gitlab/", _oauth_gitlab_callback, name="oauth_gitlab_callback_global"),
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health"),
]

from django.urls import path

from . import views

app_name = "fossil"

urlpatterns = [
    path("code/", views.code_browser, name="code"),
    path("code/tree/<path:dirpath>/", views.code_browser, name="code_dir"),
    path("code/file/<path:filepath>", views.code_file, name="code_file"),
    path("timeline/", views.timeline, name="timeline"),
    path("checkin/<str:checkin_uuid>/", views.checkin_detail, name="checkin_detail"),
    path("tickets/", views.ticket_list, name="tickets"),
    path("tickets/<str:ticket_uuid>/", views.ticket_detail, name="ticket_detail"),
    path("wiki/", views.wiki_list, name="wiki"),
    path("wiki/create/", views.wiki_create, name="wiki_create"),
    path("wiki/page/<path:page_name>", views.wiki_page, name="wiki_page"),
    path("wiki/edit/<path:page_name>", views.wiki_edit, name="wiki_edit"),
    path("tickets/create/", views.ticket_create, name="ticket_create"),
    path("forum/", views.forum_list, name="forum"),
    path("forum/<str:thread_uuid>/", views.forum_thread, name="forum_thread"),
    path("user/<str:username>/", views.user_activity, name="user_activity"),
    path("branches/", views.branch_list, name="branches"),
    path("tags/", views.tag_list, name="tags"),
    path("technotes/", views.technote_list, name="technotes"),
    path("search/", views.search, name="search"),
    path("stats/", views.repo_stats, name="stats"),
    path("compare/", views.compare_checkins, name="compare"),
    path("sync/", views.sync_pull, name="sync"),
    path("code/raw/<path:filepath>", views.code_raw, name="code_raw"),
    path("code/blame/<path:filepath>", views.code_blame, name="code_blame"),
    path("code/history/<path:filepath>", views.file_history, name="file_history"),
    path("timeline/rss/", views.timeline_rss, name="timeline_rss"),
    path("tickets/export/", views.tickets_csv, name="tickets_csv"),
    path("docs/", views.fossil_docs, name="docs"),
    path("docs/<path:doc_path>", views.fossil_doc_page, name="doc_page"),
]

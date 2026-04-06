from django.urls import path

from . import views

app_name = "fossil"

urlpatterns = [
    path("code/", views.code_browser, name="code"),
    path("code/<path:filepath>", views.code_file, name="code_file"),
    path("timeline/", views.timeline, name="timeline"),
    path("tickets/", views.ticket_list, name="tickets"),
    path("tickets/<str:ticket_uuid>/", views.ticket_detail, name="ticket_detail"),
    path("wiki/", views.wiki_list, name="wiki"),
    path("wiki/page/<path:page_name>", views.wiki_page, name="wiki_page"),
    path("forum/", views.forum_list, name="forum"),
    path("forum/<str:thread_uuid>/", views.forum_thread, name="forum_thread"),
]

from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("create/", views.project_create, name="create"),
    # Groups (before <slug:slug>/ catch-all)
    path("groups/", views.group_list, name="group_list"),
    path("groups/create/", views.group_create, name="group_create"),
    path("groups/<slug:slug>/", views.group_detail, name="group_detail"),
    path("groups/<slug:slug>/edit/", views.group_edit, name="group_edit"),
    path("groups/<slug:slug>/delete/", views.group_delete, name="group_delete"),
    # Projects
    path("<slug:slug>/", views.project_detail, name="detail"),
    path("<slug:slug>/edit/", views.project_update, name="update"),
    path("<slug:slug>/delete/", views.project_delete, name="delete"),
    path("<slug:slug>/teams/add/", views.project_team_add, name="team_add"),
    path("<slug:slug>/teams/<slug:team_slug>/edit/", views.project_team_edit, name="team_edit"),
    path("<slug:slug>/teams/<slug:team_slug>/remove/", views.project_team_remove, name="team_remove"),
]

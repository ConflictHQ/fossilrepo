from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("create/", views.project_create, name="create"),
    path("<slug:slug>/", views.project_detail, name="detail"),
    path("<slug:slug>/edit/", views.project_update, name="update"),
    path("<slug:slug>/delete/", views.project_delete, name="delete"),
    path("<slug:slug>/teams/add/", views.project_team_add, name="team_add"),
    path("<slug:slug>/teams/<slug:team_slug>/edit/", views.project_team_edit, name="team_edit"),
    path("<slug:slug>/teams/<slug:team_slug>/remove/", views.project_team_remove, name="team_remove"),
]

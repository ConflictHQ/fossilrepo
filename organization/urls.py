from django.urls import path

from . import views

app_name = "organization"

urlpatterns = [
    # Organization settings
    path("", views.org_settings, name="settings"),
    path("edit/", views.org_settings_edit, name="settings_edit"),
    # Members
    path("members/", views.member_list, name="members"),
    path("members/add/", views.member_add, name="member_add"),
    path("members/create/", views.user_create, name="user_create"),
    path("members/<str:username>/", views.user_detail, name="user_detail"),
    path("members/<str:username>/edit/", views.user_edit, name="user_edit"),
    path("members/<str:username>/password/", views.user_password, name="user_password"),
    path("members/<str:username>/remove/", views.member_remove, name="member_remove"),
    # Roles
    path("roles/", views.role_list, name="role_list"),
    path("roles/create/", views.role_create, name="role_create"),
    path("roles/initialize/", views.role_initialize, name="role_initialize"),
    path("roles/<slug:slug>/", views.role_detail, name="role_detail"),
    path("roles/<slug:slug>/edit/", views.role_edit, name="role_edit"),
    path("roles/<slug:slug>/delete/", views.role_delete, name="role_delete"),
    # Audit log
    path("audit/", views.audit_log, name="audit_log"),
    # Teams
    path("teams/", views.team_list, name="team_list"),
    path("teams/create/", views.team_create, name="team_create"),
    path("teams/<slug:slug>/", views.team_detail, name="team_detail"),
    path("teams/<slug:slug>/edit/", views.team_update, name="team_update"),
    path("teams/<slug:slug>/delete/", views.team_delete, name="team_delete"),
    path("teams/<slug:slug>/members/add/", views.team_member_add, name="team_member_add"),
    path("teams/<slug:slug>/members/<str:username>/remove/", views.team_member_remove, name="team_member_remove"),
]

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("ssh-keys/", views.ssh_keys, name="ssh_keys"),
    path("ssh-keys/<int:pk>/delete/", views.ssh_key_delete, name="ssh_key_delete"),
    path("notifications/", views.notification_preferences, name="notification_prefs"),
    # Unified profile
    path("profile/", views.profile, name="profile"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("profile/tokens/create/", views.profile_token_create, name="profile_token_create"),
    path("profile/tokens/<str:guid>/revoke/", views.profile_token_revoke, name="profile_token_revoke"),
]

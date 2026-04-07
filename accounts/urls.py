from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("ssh-keys/", views.ssh_keys, name="ssh_keys"),
    path("ssh-keys/<int:pk>/delete/", views.ssh_key_delete, name="ssh_key_delete"),
]

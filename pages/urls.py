from django.urls import path

from . import views

app_name = "pages"

urlpatterns = [
    path("", views.page_list, name="list"),
    path("create/", views.page_create, name="create"),
    path("<slug:slug>/", views.page_detail, name="detail"),
    path("<slug:slug>/edit/", views.page_update, name="update"),
    path("<slug:slug>/delete/", views.page_delete, name="delete"),
]

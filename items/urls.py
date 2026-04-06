from django.urls import path

from . import views

app_name = "items"

urlpatterns = [
    path("", views.item_list, name="list"),
    path("create/", views.item_create, name="create"),
    path("<slug:slug>/", views.item_detail, name="detail"),
    path("<slug:slug>/edit/", views.item_update, name="update"),
    path("<slug:slug>/delete/", views.item_delete, name="delete"),
]

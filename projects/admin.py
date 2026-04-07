from django.contrib import admin

from core.admin import BaseCoreAdmin

from .models import Project, ProjectTeam


class ProjectTeamInline(admin.TabularInline):
    model = ProjectTeam
    extra = 0
    raw_id_fields = ("team",)


@admin.register(Project)
class ProjectAdmin(BaseCoreAdmin):
    list_display = ("name", "slug", "visibility", "created_at", "created_by")
    list_filter = ("visibility", "created_at")
    search_fields = ("name", "slug", "description")
    inlines = [ProjectTeamInline]


@admin.register(ProjectTeam)
class ProjectTeamAdmin(BaseCoreAdmin):
    list_display = ("project", "team", "role", "created_at")
    list_filter = ("role", "team")
    search_fields = ("project__name", "team__name")
    raw_id_fields = ("project", "team")

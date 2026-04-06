from django.contrib import admin

from core.admin import BaseCoreAdmin

from .models import Project, ProjectTeam


class ProjectTeamInline(admin.TabularInline):
    model = ProjectTeam
    extra = 0
    raw_id_fields = ("team",)


@admin.register(Project)
class ProjectAdmin(BaseCoreAdmin):
    list_display = ("name", "slug", "visibility", "organization", "created_at")
    list_filter = ("visibility",)
    search_fields = ("name", "slug")
    inlines = [ProjectTeamInline]


@admin.register(ProjectTeam)
class ProjectTeamAdmin(BaseCoreAdmin):
    list_display = ("project", "team", "role", "created_at")
    list_filter = ("role",)
    raw_id_fields = ("project", "team")

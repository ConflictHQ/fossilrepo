from django.db import models

from core.models import ActiveManager, BaseCoreModel, Tracking


class ProjectGroup(BaseCoreModel):
    """Groups related projects together (e.g., Fossil SCM source + forum + docs)."""

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Project(BaseCoreModel):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        INTERNAL = "internal", "Internal"
        PRIVATE = "private", "Private"

    organization = models.ForeignKey("organization.Organization", on_delete=models.CASCADE, related_name="projects")
    group = models.ForeignKey(
        "ProjectGroup",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="projects",
        help_text="Optional group for organizing related projects",
    )
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PRIVATE)
    teams = models.ManyToManyField("organization.Team", through="ProjectTeam", blank=True, related_name="projects")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]


class ProjectTeam(Tracking):
    class Role(models.TextChoices):
        READ = "read", "Read"
        WRITE = "write", "Write"
        ADMIN = "admin", "Admin"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="project_teams")
    team = models.ForeignKey("organization.Team", on_delete=models.CASCADE, related_name="project_teams")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.READ)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ("project", "team")

    def __str__(self):
        return f"{self.project}/{self.team} ({self.role})"

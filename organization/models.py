from django.contrib.auth.models import Group
from django.db import models

from core.models import ActiveManager, BaseCoreModel, Tracking


class Organization(BaseCoreModel):
    website = models.URLField(blank=True, default="")
    groups = models.ManyToManyField(Group, blank=True, related_name="organizations")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]


class OrganizationMember(Tracking):
    is_active = models.BooleanField(default=True)
    member = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="members")
    groups = models.ManyToManyField(Group, blank=True, related_name="org_memberships")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ("member", "organization")

    def __str__(self):
        return f"{self.organization}/{self.member}"

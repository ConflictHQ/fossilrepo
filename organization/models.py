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


class OrgRole(BaseCoreModel):
    """Predefined organization role with a bundle of permissions."""

    is_default = models.BooleanField(default=False, help_text="Assigned to new users automatically")
    permissions = models.ManyToManyField("auth.Permission", blank=True, related_name="org_roles")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def apply_to_user(self, user):
        """Sync this role's permissions to a Django user via a group."""
        group, _ = Group.objects.get_or_create(name=f"role_{self.slug}")
        group.permissions.set(self.permissions.all())

        # Remove user from all role groups, then add to this one
        role_groups = Group.objects.filter(name__startswith="role_")
        user.groups.remove(*role_groups)
        user.groups.add(group)

    @staticmethod
    def remove_role_groups(user):
        """Remove all role-based groups from a user."""
        role_groups = Group.objects.filter(name__startswith="role_")
        user.groups.remove(*role_groups)


class Team(BaseCoreModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="teams")
    members = models.ManyToManyField("auth.User", blank=True, related_name="teams")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]


class OrganizationMember(Tracking):
    is_active = models.BooleanField(default=True)
    member = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="members")
    role = models.ForeignKey(
        OrgRole, null=True, blank=True, on_delete=models.SET_NULL, related_name="members", help_text="Organization role"
    )
    groups = models.ManyToManyField(Group, blank=True, related_name="org_memberships")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ("member", "organization")

    def __str__(self):
        return f"{self.organization}/{self.member}"

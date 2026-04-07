from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand

from organization.models import OrgRole

ROLE_DEFINITIONS = {
    "Admin": {
        "description": "Full access to all features",
        "is_default": False,
        "permissions": "__all__",
    },
    "Manager": {
        "description": "Manage projects, teams, and members",
        "is_default": False,
        "permissions": [
            "view_project",
            "add_project",
            "change_project",
            "delete_project",
            "view_projectteam",
            "add_projectteam",
            "change_projectteam",
            "delete_projectteam",
            "view_team",
            "add_team",
            "change_team",
            "delete_team",
            "view_organizationmember",
            "add_organizationmember",
            "change_organizationmember",
            "view_organization",
            "change_organization",
            "view_page",
            "add_page",
            "change_page",
            "delete_page",
            "view_fossilrepository",
        ],
    },
    "Developer": {
        "description": "Contribute code, create tickets and wiki pages",
        "is_default": False,
        "permissions": [
            "view_project",
            "add_project",
            "view_team",
            "view_organizationmember",
            "view_organization",
            "view_fossilrepository",
            "view_page",
            "add_page",
        ],
    },
    "Viewer": {
        "description": "Read-only access to all content",
        "is_default": True,
        "permissions": [
            "view_project",
            "view_projectteam",
            "view_team",
            "view_organizationmember",
            "view_organization",
            "view_fossilrepository",
            "view_page",
        ],
    },
}


class Command(BaseCommand):
    help = "Create default organization roles"

    def handle(self, *args, **options):
        for name, config in ROLE_DEFINITIONS.items():
            role, created = OrgRole.objects.get_or_create(
                slug=name.lower(),
                defaults={
                    "name": name,
                    "description": config["description"],
                    "is_default": config["is_default"],
                },
            )

            if not created:
                role.description = config["description"]
                role.is_default = config["is_default"]
                role.save()

            if config["permissions"] == "__all__":
                perms = Permission.objects.filter(content_type__app_label__in=["organization", "projects", "pages", "fossil"])
            else:
                perms = Permission.objects.filter(codename__in=config["permissions"])

            role.permissions.set(perms)
            status = "created" if created else "updated"
            self.stdout.write(f"  {status}: {name} ({role.permissions.count()} permissions)")

        self.stdout.write(self.style.SUCCESS("Done."))

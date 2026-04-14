"""ensure_user — idempotent user bootstrap for trusted-auth deployments.

Called from the ECS entrypoint when JUPYTERHUB_USER is set, so the
Django user exists before the first request hits the app.

Usage:
    python manage.py ensure_user --username lmata
    python manage.py ensure_user --username lmata --email lmata@example.com --group Administrators
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Ensure a user exists, creating them as a superuser if not. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Username to ensure exists")
        parser.add_argument("--email", default="", help="Email address (used only on creation)")
        parser.add_argument(
            "--group",
            action="append",
            dest="groups",
            default=[],
            metavar="GROUP",
            help="Add user to this group (repeatable). Group is created if it does not exist.",
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        username = options["username"]

        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": options["email"],
                "is_staff": True,
                "is_superuser": True,
            },
        )

        if created:
            user.set_unusable_password()
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Created superuser: {username}"))
        else:
            self.stdout.write(f"User already exists: {username}")

        for group_name in options["groups"]:
            group, group_created = Group.objects.get_or_create(name=group_name)
            user.groups.add(group)
            if group_created:
                self.stdout.write(f"  Created group and added user: {group_name}")
            else:
                self.stdout.write(f"  Added user to group: {group_name}")

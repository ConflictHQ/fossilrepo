import logging

from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand

from items.models import Item
from organization.models import Organization, OrganizationMember

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Seed the database with initial data for development."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Flush non-system tables before seeding.")

    def handle(self, *args, **options):
        if options["flush"]:
            self.stdout.write("Flushing item and organization data...")
            Item.all_objects.all().delete()
            OrganizationMember.all_objects.all().delete()
            Organization.all_objects.all().delete()

        # Groups and permissions
        admin_group, _ = Group.objects.get_or_create(name="Administrators")
        viewer_group, _ = Group.objects.get_or_create(name="Viewers")

        item_perms = Permission.objects.filter(content_type__app_label="items")
        admin_group.permissions.set(item_perms)
        view_perms = item_perms.filter(codename__startswith="view_")
        viewer_group.permissions.set(view_perms)

        org_perms = Permission.objects.filter(content_type__app_label="organization")
        admin_group.permissions.add(*org_perms)

        # Superuser
        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@fossilrepo.local", "is_staff": True, "is_superuser": True},
        )
        if created:
            admin_user.set_password("admin")
            admin_user.save()
            self.stdout.write(self.style.SUCCESS("Created superuser: admin / admin"))

        # Regular user
        viewer_user, created = User.objects.get_or_create(
            username="viewer",
            defaults={"email": "viewer@fossilrepo.local", "is_staff": False, "is_superuser": False},
        )
        if created:
            viewer_user.set_password("viewer")
            viewer_user.save()
            viewer_user.groups.add(viewer_group)
            self.stdout.write(self.style.SUCCESS("Created viewer user: viewer / viewer"))

        # Organization
        org, _ = Organization.objects.get_or_create(name="Fossilrepo HQ", defaults={"description": "Default organization"})
        OrganizationMember.objects.get_or_create(member=admin_user, organization=org)
        OrganizationMember.objects.get_or_create(member=viewer_user, organization=org)

        # Sample items
        items_data = [
            {"name": "Widget Alpha", "price": "29.99", "sku": "WGT-001", "description": "A versatile alpha widget."},
            {"name": "Widget Beta", "price": "49.99", "sku": "WGT-002", "description": "Enhanced beta widget with extra features."},
            {"name": "Gadget Pro", "price": "199.99", "sku": "GDG-001", "description": "Professional-grade gadget."},
            {"name": "Starter Kit", "price": "9.99", "sku": "KIT-001", "description": "Everything you need to get started."},
            {"name": "Premium Bundle", "price": "399.99", "sku": "BDL-001", "description": "Our best items in one bundle."},
        ]
        for data in items_data:
            Item.objects.get_or_create(
                sku=data["sku"],
                defaults={**data, "created_by": admin_user},
            )

        self.stdout.write(self.style.SUCCESS("Seed complete."))

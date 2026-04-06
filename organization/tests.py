import pytest
from django.contrib.auth.models import User

from .models import Organization, OrganizationMember


@pytest.mark.django_db
class TestOrganization:
    def test_create_organization(self):
        org = Organization.objects.create(name="Acme Corp")
        assert org.slug == "acme-corp"
        assert org.guid is not None

    def test_soft_delete_excludes_from_default_manager(self):
        user = User.objects.create_user(username="test", password="x")
        org = Organization.objects.create(name="DeleteMe")
        org.soft_delete(user=user)
        assert Organization.objects.filter(slug="deleteme").count() == 0
        assert Organization.all_objects.filter(slug="deleteme").count() == 1


@pytest.mark.django_db
class TestOrganizationMember:
    def test_create_membership(self, admin_user, org):
        assert OrganizationMember.objects.filter(member=admin_user, organization=org).exists()

    def test_unique_membership(self, admin_user, org):
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            OrganizationMember.objects.create(member=admin_user, organization=org)

    def test_str_representation(self, admin_user, org):
        member = OrganizationMember.objects.get(member=admin_user, organization=org)
        assert str(member) == f"{org}/{admin_user}"

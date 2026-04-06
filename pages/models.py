from django.db import models

from core.models import ActiveManager, BaseCoreModel


class Page(BaseCoreModel):
    content = models.TextField(blank=True, default="")
    is_published = models.BooleanField(default=True)
    organization = models.ForeignKey("organization.Organization", on_delete=models.CASCADE, related_name="pages")

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["name"]

from django.apps import AppConfig


class FossilConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fossil"

    def ready(self):
        import fossil.signals  # noqa: F401

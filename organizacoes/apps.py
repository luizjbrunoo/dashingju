from django.apps import AppConfig


class OrganizacoesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "organizacoes"
    verbose_name = "Organizações"

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(
            _on_post_migrate_sync_rbac,
            dispatch_uid="organizacoes.sync_managed_rbac_groups",
        )


def _on_post_migrate_sync_rbac(sender, **kwargs):
    from organizacoes.role_capabilities import sync_managed_rbac_groups

    sync_managed_rbac_groups()

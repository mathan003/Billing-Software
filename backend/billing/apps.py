import os
from django.apps import AppConfig
from django.db.models.signals import post_migrate


def auto_seed_default_users(sender, **kwargs):
    """
    Automatically creates default administrator and operator accounts upon migration
    so Railway cloud deployments and fresh databases are immediately accessible.
    """
    try:
        from django.contrib.auth.models import User
        from .models import UserProfile, CompanySettings, Branch, SoftwareUpdate

        # 1. Initialize Company Settings
        CompanySettings.get_settings()

        # 2. Initialize Default Branch
        Branch.get_default_branch()

        # 3. Ensure 'admin' Superuser exists
        admin_user, created = User.objects.get_or_create(username="admin")
        if created or not admin_user.has_usable_password():
            admin_user.set_password(os.getenv("ADMIN_PASSWORD", "admin123"))
            admin_user.is_superuser = True
            admin_user.is_staff = True
            admin_user.email = "admin@mathanhub.com"
            admin_user.save()

        prof_admin, _ = UserProfile.objects.get_or_create(user=admin_user)
        prof_admin.role = "admin"
        prof_admin.device_limit = 2
        prof_admin.save()

        # 4. Ensure 'Mathan003' Admin exists
        mathan_user, m_created = User.objects.get_or_create(username="Mathan003")
        if m_created or not mathan_user.has_usable_password():
            mathan_user.set_password(os.getenv("MATHAN_PASSWORD", "M@th@n93612003"))
            mathan_user.is_superuser = True
            mathan_user.is_staff = True
            mathan_user.email = "mathan003m@gmail.com"
            mathan_user.save()

        prof_mathan, _ = UserProfile.objects.get_or_create(user=mathan_user)
        prof_mathan.role = "admin"
        prof_mathan.device_limit = 2
        prof_mathan.save()

        # 5. Check DeletedClient blacklist to guarantee deleted clients are NEVER recreated on restart/migration
        from .models import DeletedClient
        deleted_names = set(DeletedClient.objects.values_list("username", flat=True))

        # Only seed demo client 'operator1' if fresh system, never deleted, and no clients exist
        has_any_client = UserProfile.objects.filter(role="client", is_deleted=False).exists()
        if not has_any_client and "operator1" not in deleted_names:
            operator_user, o_created = User.objects.get_or_create(username="operator1")
            if o_created or not operator_user.has_usable_password():
                operator_user.set_password("operator123")
                operator_user.is_staff = False
                operator_user.save()

            prof_op, _ = UserProfile.objects.get_or_create(user=operator_user)
            prof_op.role = "client"
            prof_op.shop_name = "MathanHub Branch 1"
            prof_op.device_limit = 5
            prof_op.access_mode = "online_offline"
            prof_op.is_deleted = False
            prof_op.save()

        # 7. Auto-publish Current Code Version to SoftwareUpdate table
        from .version import APP_VERSION, APP_TITLE, APP_RELEASE_NOTES
        SoftwareUpdate.objects.update_or_create(
            version=APP_VERSION,
            defaults={
                "title": APP_TITLE,
                "release_notes": APP_RELEASE_NOTES,
                "is_published": True,
            }
        )

    except Exception:
        pass


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"

    def ready(self):
        post_migrate.connect(auto_seed_default_users, sender=self)


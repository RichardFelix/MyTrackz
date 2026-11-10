from django.db import migrations
from django.db.models import Q


def flag_owner(apps, schema_editor):
    """Store ownership on the account that used to be the calculated owner.

    Instance admin rights used to fall to the oldest active non-demo account
    when nobody was staff. They are now a stored flag, so on installs without
    a usable staff/superuser account that same account is marked staff.
    """
    User = apps.get_model("users", "User")
    real_accounts = User.objects.filter(is_active=True, is_demo=False)
    if real_accounts.filter(Q(is_staff=True) | Q(is_superuser=True)).exists():
        return
    owner = real_accounts.order_by("id").first()
    if owner is not None:
        owner.is_staff = True
        owner.save(update_fields=["is_staff"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0073_user_must_change_password"),
    ]

    operations = [
        # Never un-flag on reverse: the account may have been promoted by hand.
        migrations.RunPython(flag_owner, migrations.RunPython.noop),
    ]

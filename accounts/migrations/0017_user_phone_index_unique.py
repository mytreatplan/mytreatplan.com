"""No two accounts share a mobile (epic-doctor-profile entry 3, decision of 2026-11-15):
a unique rule on `phone_index` among the accounts that have one (erased accounts and
accounts without a mobile have a blank index).

Before the rule is added, the migration looks for accounts that already share a mobile.
If any do, it stops and names their ids (never the number), and nothing is changed: give
each account a different mobile (or erase the one that is not used), then migrate again
(deploy/README.md, "Unique mobile")."""
from django.db import migrations, models


def stop_on_shared_mobiles(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    shared = (User.objects.exclude(phone_index='').values('phone_index')
              .annotate(count=models.Count('pk')).filter(count__gt=1)
              .values_list('phone_index', flat=True))
    groups = []
    for index in shared:
        pks = sorted(User.objects.filter(phone_index=index).values_list('pk', flat=True))
        groups.append(', '.join(str(pk) for pk in pks))
    if groups:
        raise RuntimeError(
            'These accounts share a mobile (each group one number): '
            + '; '.join(sorted(groups))
            + '. Give each a different mobile, or erase the unused account, then migrate '
            'again.')


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_pending_contact_change'),
    ]

    operations = [
        migrations.RunPython(stop_on_shared_mobiles, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(condition=models.Q(('phone_index', ''), _negated=True), fields=('phone_index',), name='accounts_user_phone_index_unique'),
        ),
    ]

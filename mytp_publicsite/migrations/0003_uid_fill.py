# AD-5 (story 1.8), step 2 of 3: give every existing row of contact messages its own uid
# (uuid4, in batches). A migration of its own: PostgreSQL must not alter the table in the
# same transaction as this data change. Going back needs nothing (step 1 drops it).

from django.db import migrations

from mytreatplan.uids import fill_uids


class Migration(migrations.Migration):

    dependencies = [
        ('mytp_publicsite', '0002_uid_add'),
    ]

    operations = [
        migrations.RunPython(fill_uids('mytp_publicsite', 'contactmessage'),
                             migrations.RunPython.noop),
    ]

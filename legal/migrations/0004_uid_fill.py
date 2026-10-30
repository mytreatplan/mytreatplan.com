# AD-5 (story 1.8), step 2 of 3: give every existing row of legal documents and legal
# choices its own uid (uuid4, in batches). A migration of its own: PostgreSQL must not
# alter the table in the same transaction as this data change. Going back needs nothing
# (step 1 drops it).

from django.db import migrations

from mytreatplan.uids import fill_uids


class Migration(migrations.Migration):

    dependencies = [
        ('legal', '0003_uid_add'),
    ]

    operations = [
        migrations.RunPython(fill_uids('legal', 'legaldocument'), migrations.RunPython.noop),
        migrations.RunPython(fill_uids('legal', 'legalchoice'), migrations.RunPython.noop),
    ]

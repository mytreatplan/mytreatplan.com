# Signup entry 6 (story 2.6): pending registrations saved before the legal step existed
# are past it (step "verify-email") with no legal choices; the email code would create
# the account without any acceptance. Send them back to the legal step.

from django.db import migrations

# Steps up to and including the legal step (signup.steps.STEPS at this migration).
NOT_PAST_LEGAL = ('account', 'legal')


def back_to_legal(apps, schema_editor):
    PendingRegistration = apps.get_model('signup', 'PendingRegistration')
    LegalChoice = apps.get_model('legal', 'LegalChoice')
    with_choices = LegalChoice.objects.filter(pending_registration__isnull=False).values(
        'pending_registration')
    (PendingRegistration.objects.exclude(step__in=NOT_PAST_LEGAL)
     .exclude(pk__in=with_choices).update(step='legal'))


class Migration(migrations.Migration):

    dependencies = [
        ('signup', '0007_pendingregistration_email_verified_at'),
        ('legal', '0002_draft_documents'),
    ]

    operations = [
        migrations.RunPython(back_to_legal, migrations.RunPython.noop),
    ]

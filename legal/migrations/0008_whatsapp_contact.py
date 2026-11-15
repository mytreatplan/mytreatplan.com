# Epic-doctor-profile entry 2: the WhatsApp contact consent, a new kind of legal document
# chosen on the doctor's personal-data page (never at signup: LegalDocument.SIGNUP_KINDS).
#
# The new choice changes no column (the kind is a plain varchar). Version 1 is published
# now in the instance region (settings.REGION_PROFILE at migrate time), provisional, with
# short placeholder texts marked "Draft" until the lawyer's text is added in the admin as
# version 2. Nothing is created when the region already has a version of the kind.
# Going back deletes version 1 of the kind in the instance region if nobody chose on it
# (a choice PROTECTs it: then the migration cannot be reversed).

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone

KIND = 'whatsapp_contact'

DRAFT_EN = ('**Draft.** This is a placeholder, not the final legal text. It will be replaced '
            'by the final version before launch.\n\n')
DRAFT_ES = ('**Borrador.** Este es un texto provisional, no el texto legal definitivo. Se '
            'sustituirá por la versión definitiva antes del lanzamiento.\n\n')

DOCUMENT = {
    'kind': KIND,
    'title_en': 'WhatsApp contact (draft)',
    'title_es': 'Contacto por WhatsApp (borrador)',
    'body_en': DRAFT_EN + (
        '## WhatsApp contact\n\n'
        'I agree to be contacted by MyTreatPlan via WhatsApp.\n\n'
        'This consent is optional. You can withdraw it at any time in your personal '
        'data.\n'
    ),
    'body_es': DRAFT_ES + (
        '## Contacto por WhatsApp\n\n'
        'Doy mi permiso para que contacten conmigo vía WhatsApp.\n\n'
        'Este consentimiento es opcional. Puede retirarlo en cualquier momento desde sus '
        'datos personales.\n'
    ),
}


def publish_draft(apps, schema_editor):
    LegalDocument = apps.get_model('legal', 'LegalDocument')
    region = settings.REGION_PROFILE.code
    documents = LegalDocument._base_manager.using(schema_editor.connection.alias)
    if documents.filter(kind=KIND, region__in=(region, '')).exists():
        return
    documents.create(version=1, region=region, provisional=True,
                     published_at=timezone.now(), **DOCUMENT)


def remove_draft(apps, schema_editor):
    LegalDocument = apps.get_model('legal', 'LegalDocument')
    LegalDocument._base_manager.using(schema_editor.connection.alias).filter(
        kind=KIND, version=1, region=settings.REGION_PROFILE.code).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('legal', '0007_region_fill'),
    ]

    operations = [
        migrations.AlterField(
            model_name='legaldocument',
            name='kind',
            field=models.CharField(choices=[
                ('terms', 'Terms'), ('conditions', 'Conditions'),
                ('ormco_tps', 'Ormco TPS contract'),
                ('commercial_consent', 'Commercial consent'),
                ('whatsapp_contact', 'WhatsApp contact consent')], max_length=30),
        ),
        migrations.RunPython(publish_draft, remove_draft),
    ]

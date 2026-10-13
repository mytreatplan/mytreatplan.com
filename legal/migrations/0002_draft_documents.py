# Signup entry 6 (story 2.6): version 1 of each legal document, published now. Short
# placeholders written by us, in English and Spanish, clearly marked "Draft", until the
# lawyer's texts are added in the admin as version 2.

from django.db import migrations
from django.utils import timezone

GROUP_EN = (
    '- **MyTPDSO Limited** (Ireland)\n'
    '- **MyTPDSO Spain SL** (Spain)\n'
    '- **MyTPDSO LLC-FZ** (United Arab Emirates)\n'
)
GROUP_ES = (
    '- **MyTPDSO Limited** (Irlanda)\n'
    '- **MyTPDSO Spain SL** (España)\n'
    '- **MyTPDSO LLC-FZ** (Emiratos Árabes Unidos)\n'
)

DRAFT_EN = ('**Draft.** This is a placeholder, not the final legal text. It will be replaced '
            'by the final version before launch.\n\n')
DRAFT_ES = ('**Borrador.** Este es un texto provisional, no el texto legal definitivo. Se '
            'sustituirá por la versión definitiva antes del lanzamiento.\n\n')

DOCUMENTS = [
    {
        'kind': 'terms',
        'title_en': 'Terms of Use (draft)',
        'title_es': 'Términos de uso (borrador)',
        'body_en': DRAFT_EN + (
            '## Terms of Use\n\n'
            'These terms govern your use of the MyTreatPlan portal. By creating an account '
            'you confirm that you are a licensed dental professional and that the details '
            'you give us are accurate.\n\n'
            '- Keep your password secret; you are responsible for activity on your account.\n'
            '- Use the portal only for the treatment planning of your own patients.\n'
        ),
        'body_es': DRAFT_ES + (
            '## Términos de uso\n\n'
            'Estos términos regulan el uso del portal MyTreatPlan. Al crear una cuenta, '
            'usted confirma que es un profesional dental colegiado y que los datos que nos '
            'facilita son correctos.\n\n'
            '- Mantenga su contraseña en secreto; usted es responsable de la actividad de '
            'su cuenta.\n'
            '- Utilice el portal solo para la planificación de tratamientos de sus propios '
            'pacientes.\n'
        ),
    },
    {
        'kind': 'conditions',
        'title_en': 'Conditions of Service (draft)',
        'title_es': 'Condiciones del servicio (borrador)',
        'body_en': DRAFT_EN + (
            '## Conditions of Service\n\n'
            'These conditions describe how MyTreatPlan provides its treatment planning '
            'services: how cases are submitted, how plans are delivered and how fees, '
            'when they apply, are charged.\n'
        ),
        'body_es': DRAFT_ES + (
            '## Condiciones del servicio\n\n'
            'Estas condiciones describen cómo MyTreatPlan presta sus servicios de '
            'planificación de tratamientos: cómo se envían los casos, cómo se entregan los '
            'planes y cómo se cobran las tarifas, cuando correspondan.\n'
        ),
    },
    {
        'kind': 'ormco_tps',
        'title_en': 'Ormco TPS contract (draft)',
        'title_es': 'Contrato Ormco TPS (borrador)',
        'body_en': DRAFT_EN + (
            '## Ormco TPS contract\n\n'
            'This optional contract covers treatment planning services provided through '
            'Ormco TPS. You can sign up without accepting it.\n'
        ),
        'body_es': DRAFT_ES + (
            '## Contrato Ormco TPS\n\n'
            'Este contrato opcional cubre los servicios de planificación de tratamientos '
            'prestados a través de Ormco TPS. Puede registrarse sin aceptarlo.\n'
        ),
    },
    {
        'kind': 'commercial_consent',
        'title_en': 'Commercial communications (draft)',
        'title_es': 'Comunicaciones comerciales (borrador)',
        'body_en': DRAFT_EN + (
            '## Commercial communications\n\n'
            'With your consent, the companies of the MyTPDSO group may send you news and '
            'offers about their products and services:\n\n'
            + GROUP_EN +
            '\nThey may contact you by **email, WhatsApp, SMS and phone**. Your data is '
            'shared only within the group and is never shared outside it. This consent '
            'is optional: you can sign up without giving it.\n'
        ),
        'body_es': DRAFT_ES + (
            '## Comunicaciones comerciales\n\n'
            'Con su consentimiento, las empresas del grupo MyTPDSO podrán enviarle '
            'novedades y ofertas sobre sus productos y servicios:\n\n'
            + GROUP_ES +
            '\nPodrán contactarle por **correo electrónico, WhatsApp, SMS y teléfono**. '
            'Sus datos solo se comparten dentro del grupo y nunca se comparten fuera de '
            'él. Este consentimiento es opcional: puede registrarse sin darlo.\n'
        ),
    },
]


def publish_drafts(apps, schema_editor):
    LegalDocument = apps.get_model('legal', 'LegalDocument')
    now = timezone.now()
    for document in DOCUMENTS:
        if not LegalDocument.objects.filter(kind=document['kind']).exists():
            LegalDocument.objects.create(version=1, published_at=now, **document)


def remove_drafts(apps, schema_editor):
    LegalDocument = apps.get_model('legal', 'LegalDocument')
    LegalDocument.objects.filter(version=1, kind__in=[d['kind'] for d in DOCUMENTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('legal', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(publish_drafts, remove_drafts),
    ]

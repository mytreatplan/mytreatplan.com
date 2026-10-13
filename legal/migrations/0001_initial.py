# Signup entry 6 (story 2.6): versioned legal documents and the choices made on them.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('signup', '0007_pendingregistration_email_verified_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LegalDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('terms', 'Terms'), ('conditions', 'Conditions'), ('ormco_tps', 'Ormco TPS contract'), ('commercial_consent', 'Commercial consent')], max_length=30)),
                ('version', models.PositiveIntegerField(editable=False)),
                ('title_en', models.CharField(max_length=200, verbose_name='title (English)')),
                ('title_es', models.CharField(max_length=200, verbose_name='title (Spanish)')),
                ('body_en', models.TextField(help_text='Markdown.', verbose_name='body (English)')),
                ('body_es', models.TextField(help_text='Markdown.', verbose_name='body (Spanish)')),
                ('published_at', models.DateTimeField(blank=True, help_text='When this version applies. Leave empty for a draft. Once it has passed, this version can no longer be changed or deleted.', null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['kind', '-version'],
                'constraints': [models.UniqueConstraint(fields=('kind', 'version'), name='legal_document_kind_version')],
            },
        ),
        migrations.CreateModel(
            name='LegalChoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('accepted', models.BooleanField()),
                ('decided_at', models.DateTimeField()),
                ('pending_registration', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='legal_choices', to='signup.pendingregistration')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='legal_choices', to=settings.AUTH_USER_MODEL)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='choices', to='legal.legaldocument')),
            ],
            options={
                'ordering': ['decided_at', 'id'],
                'constraints': [models.CheckConstraint(condition=models.Q(models.Q(('pending_registration__isnull', False), ('user__isnull', True)), models.Q(('pending_registration__isnull', True), ('user__isnull', False)), _connector='OR'), name='legal_choice_one_owner')],
            },
        ),
    ]

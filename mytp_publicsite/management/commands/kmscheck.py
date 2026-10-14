"""Check the server can use its KMS service key (story 1.15).

    python manage.py kmscheck

Run daily by the `kmscheck` job in deploy/jobs.conf (through `manage.py runjob`, so a
failed or missed run raises a Sentry alert). It:

1. checks the KMS_* settings and credential files (no KMS call when one is missing);
2. reads the client certificate's expiry with `openssl x509`;
3. gets the service key (id and state), generates a 256-bit data key, unwraps it and
   checks the unwrapped bytes equal the original;
4. unwraps every configured field key (story 2.12: each FIELD_KMS_KEYS entry and
   BLIND_INDEX_KMS_KEY), printing each key id with ok or failed, and fails if one cannot
   be unwrapped (so the daily job alerts before the app needs that key);
5. fails if the client certificate expires within CERT_RENEW_DAYS days, after the
   round trip, so Sentry alerts ahead of expiry.

It prints the key id and state, the round-trip result, the field key ids with their
result and the certificate's expiry.
It never prints the data key (plain or wrapped) or credential material, and errors
name the step (settings, cert, connect, auth, get, generate, unwrap, mismatch) without
chaining the original exception.
"""
import hmac
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts import crypto
from mytp_publicsite import kms

CERT_RENEW_DAYS = 30
DATA_KEY_NAME = 'mytp-kmscheck'
DATA_KEY_BITS = 256


class FieldKeySettingsError(Exception):
    """FIELD_KMS_KEYS or BLIND_INDEX_KMS_KEY is missing or malformed (names the setting)."""


def now():
    return datetime.now(timezone.utc)


class Command(BaseCommand):
    help = 'Wrap and unwrap a test data key with the KMS service key; check the certificate expiry.'

    def handle(self, **options):
        try:
            client = kms.KMSClient.from_settings(settings)
            expiry = kms.certificate_expiry(client.cert_file)
        except kms.KMSError as exc:
            raise CommandError(f'kmscheck: {exc}') from None
        days_left = (expiry - now()).days
        expiry_text = f'{expiry:%Y-%m-%d %H:%M} UTC'
        self.stdout.write(f'kmscheck: client certificate expires {expiry_text} ({days_left} days)')

        key_id = settings.KMS_SERVICE_KEY_ID.strip()
        try:
            info = client.get_service_key(key_id)
            # The KMS returns the state under `attributes` (OKMS REST API 2.20).
            state = (info.get('attributes') or {}).get('state', 'unknown')
            self.stdout.write(f'kmscheck: service key {kms.safe_text(info.get("id", key_id))} '
                              f'state {kms.safe_text(state)}')
            wrapped, plaintext = client.generate_data_key(key_id, DATA_KEY_NAME, DATA_KEY_BITS)
            unwrapped = client.decrypt_data_key(key_id, wrapped)
        except kms.KMSError as exc:
            raise CommandError(f'kmscheck: {exc}') from None
        same = len(plaintext) == DATA_KEY_BITS // 8 and hmac.compare_digest(plaintext, unwrapped)
        del wrapped, plaintext, unwrapped
        if not same:
            raise CommandError('kmscheck: step mismatch failed: the unwrapped data key differs '
                               'from the generated one')
        self.stdout.write('kmscheck: round trip ok')

        # Both failures are collected, so a field key failure never hides "renew it".
        failures = []
        field_failure = self.check_field_keys(client, key_id)
        if field_failure:
            failures.append(field_failure)
        if expiry - now() < timedelta(days=CERT_RENEW_DAYS):
            failures.append(
                f'client certificate expires {expiry_text}, within {CERT_RENEW_DAYS} '
                f'days; renew it (deploy/README.md, "Keys (KMS)")')
        if failures:
            raise CommandError(f'kmscheck: {"; ".join(failures)}')

    def configured_field_keys(self):
        """(label, key id, wrapped) for FIELD_KMS_KEYS and BLIND_INDEX_KMS_KEY. Both are
        required with the KMS provider; otherwise only what is set is checked."""
        uses_kms = (getattr(settings, 'FIELD_KEY_PROVIDER', '') or '') == crypto.KMS_PROVIDER
        field_keys = getattr(settings, 'FIELD_KMS_KEYS', '') or ''
        index_key = getattr(settings, 'BLIND_INDEX_KMS_KEY', '') or ''
        keys = []
        try:
            if uses_kms or field_keys.strip():
                keys += [('field key', key_id, wrapped) for key_id, wrapped
                         in crypto.parse_wrapped_key_list(field_keys, 'FIELD_KMS_KEYS')]
            if uses_kms or index_key.strip():
                keys.append(('blind index key', crypto.KMSKeyProvider.blind_index_key_id,
                             crypto.KMSKeyProvider.check_blind_index_key()))
        except crypto.KeyConfigurationError as exc:
            raise FieldKeySettingsError(f'step settings failed: {exc}') from None
        return keys

    def check_field_keys(self, client, service_key_id):
        """Unwrap every configured field key; the failure message, or None."""
        try:
            keys = self.configured_field_keys()
        except FieldKeySettingsError as exc:
            return str(exc)
        if not keys:
            self.stdout.write('kmscheck: no KMS field keys configured (FIELD_KMS_KEYS, '
                              'BLIND_INDEX_KMS_KEY)')
            return None
        failed = []
        for label, key_id, wrapped in keys:
            try:
                plain = client.decrypt_data_key(service_key_id, wrapped)
            except kms.KMSError as exc:
                result = f'failed (step {exc.step}' + (
                    f', HTTP {exc.status})' if exc.status else ')')
            else:
                result = 'ok' if len(plain) == crypto.KEY_SIZE else 'failed (wrong size)'
                del plain
            del wrapped
            self.stdout.write(f'kmscheck: {label} {key_id} unwrap {result}')
            if result != 'ok':
                failed.append(key_id)
        if failed:
            return f'step field-keys failed: cannot unwrap {", ".join(failed)}'
        return None

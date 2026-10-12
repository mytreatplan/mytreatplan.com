"""Check the server can use its KMS service key (story 1.15).

    python manage.py kmscheck

Run daily by the `kmscheck` job in deploy/jobs.conf (through `manage.py runjob`, so a
failed or missed run raises a Sentry alert). It:

1. checks the KMS_* settings and credential files (no KMS call when one is missing);
2. reads the client certificate's expiry with `openssl x509`;
3. gets the service key (id and state), generates a 256-bit data key, unwraps it and
   checks the unwrapped bytes equal the original;
4. fails if the client certificate expires within CERT_RENEW_DAYS days, after the
   round trip, so Sentry alerts ahead of expiry.

It prints the key id and state, the round-trip result and the certificate's expiry.
It never prints the data key (plain or wrapped) or credential material, and errors
name the step (settings, cert, connect, auth, get, generate, unwrap, mismatch) without
chaining the original exception.
"""
import hmac
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from mytp_publicsite import kms

CERT_RENEW_DAYS = 30
DATA_KEY_NAME = 'mytp-kmscheck'
DATA_KEY_BITS = 256


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
            self.stdout.write(f'kmscheck: service key {kms.safe_text(info.get("id", key_id))} '
                              f'state {kms.safe_text(info.get("state", "unknown"))}')
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

        if expiry - now() < timedelta(days=CERT_RENEW_DAYS):
            raise CommandError(
                f'kmscheck: client certificate expires {expiry_text}, within {CERT_RENEW_DAYS} '
                f'days; renew it (deploy/README.md, "Keys (KMS)")')

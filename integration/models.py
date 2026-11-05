"""The log of the calls this site receives on /api/ (epic-admin-interface entry 1, AD-4).

One row per request to /api/, accepted or refused, written by
integration.calls.InterfaceCallMiddleware. `iss` and `kid` are what the token claimed
(unverified when the call was refused); never the token itself. Kept
RETENTION_DAYS days, then deleted by the daily `purgeinterfacecalls` job
(`manage.py purge_interface_calls`).
"""
from django.db import models
from django.utils import timezone

# Decision (user, 2026-11-05): call logs are kept 90 days.
RETENTION_DAYS = 90


class InterfaceCall(models.Model):
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    iss = models.CharField('issuer', max_length=64, blank=True)
    kid = models.CharField('key id', max_length=64, blank=True)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=512)
    accepted = models.BooleanField(default=False)
    # A refusal code of mytp_core.interface (missing, signature, unknown_key, audience,
    # expired, request_hash, replayed), `unavailable` (no signing key) or `not_checked`
    # (no signed view answered: an unknown path or method); empty when accepted.
    reason = models.CharField(max_length=32, blank=True)
    status = models.PositiveSmallIntegerField()
    # The caller's IP (eventlog.service.client_ip); text, as it is not checked.
    ip = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        outcome = 'accepted' if self.accepted else f'refused ({self.reason})'
        return f'{self.method} {self.path} from {self.iss or "?"} {outcome}'

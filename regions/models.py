"""The country map (epic-regions entry 1, AD-3, AD-14): which region serves a country.

One row per ISO 3166-1 alpha-2 code with its state: a region code (`eu`, `ae`) or
`banned`. Read-only on the portal (no admin, no edit UI); until epic-catalogue a change is
a data migration, later the admin site's configuration snapshot replaces the rows by code.
Not marked CROSSES_SITES: the code is the key on every site. Read it through
regions.countries.lookup_country, never by comparing with a region name elsewhere.
"""
from django.db import models

from regions.profiles import PROFILES

BANNED = 'banned'

STATE_CHOICES = [(code, code) for code in PROFILES] + [(BANNED, BANNED)]


class Country(models.Model):
    code = models.CharField(max_length=2, primary_key=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES)

    class Meta:
        verbose_name = 'country'
        verbose_name_plural = 'country map'
        ordering = ['code']

    def __str__(self):
        return f'{self.code}: {self.state}'

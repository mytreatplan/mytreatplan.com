# Brownfield

What the repo looked like when this spec was written (2026-10-08):

- Django 6.1.1 project `mytreatplan` (settings, urls, asgi/wsgi).
- Database: PostgreSQL via `psycopg` 3 and `dj-database-url`. `DATABASE_URL` is read from `settings.env` through `python-decouple`.
- App `mytp_publicsite`, empty (no models or views yet).
- `TIME_ZONE = 'Europe/Madrid'`, `USE_I18N = True`, `LANGUAGE_CODE = 'en-us'`.
- No custom user model yet (`AUTH_USER_MODEL` not set). Set one before the first migration that creates users, because changing it later is costly in Django.
- The public landing (currently at mytreatplan.ae) is integrated into this project before signup work starts.

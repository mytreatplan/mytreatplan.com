# Encryption Policy

Chosen: application-level field encryption with KMS envelope keys (option B of the original proposal).

| Layer | Rule |
|---|---|
| Personal fields | Encrypted in the app with AES-256-GCM before reaching the DB. Covers names, email, phone, and the "Others" text. Covers pending registrations as well as users |
| Keys | Envelope encryption: data keys wrapped by a master key in a managed KMS (AWS KMS, GCP KMS, Azure Key Vault or HashiCorp Vault; to be chosen with hosting). Keys never stored with the data; key rotation supported |
| Uniqueness and lookups | Blind index: HMAC-SHA256 of the normalized email and phone, with a separate key, stored next to the ciphertext. CAP-2, CAP-3 and login lookups use it |
| Passwords | Argon2id hash (supported natively by Django), never encrypted |
| Storage | Disk/volume encryption underneath, as the base layer |
| Transport | TLS everywhere, including to the DB and Twilio |
| Logs | Signup logs hold personal data for up to 30 days and get the same protection; after that, anonymized |

## Rejected options

- **pgcrypto in SQL:** keys pass through SQL and can leak to DB logs.
- **Disk encryption alone:** does nothing against a breach of the running DB.

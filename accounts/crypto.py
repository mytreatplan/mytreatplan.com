"""Field encryption and the blind index: a re-export of `mytp_core.crypto` (epic-platform
entry 4, architecture AD-10), where the code moved unchanged in behaviour.

Kept because:
- migrations (accounts 0003, 0004, 0006; signup 0002, 0003, 0006; mytp_publicsite 0001;
  eventlog 0001) import `accounts.crypto.Encrypted*Field` and `encrypt_existing_rows`;
- live settings.env files say FIELD_KEY_PROVIDER=accounts.crypto.KMSKeyProvider, and the
  portal's settings default, runbook and examples name the `accounts.crypto` paths. Both
  paths resolve to the same classes; the KMS provider is recognised by class
  (`mytp_core.crypto.uses_kms_provider`), not by its dotted path.

New code may import from either; the stored format, keys and messages are the package's.
"""
from mytp_core.crypto import *  # noqa: F401,F403
from mytp_core.crypto import _rewrite_rows, _split_key_list  # noqa: F401

# The portal's documented provider paths (mytreatplan/settings.py default, settings.env
# files, deploy/README.md). The package's own constants are mytp_core.crypto.*.
DEFAULT_KEY_PROVIDER = 'accounts.crypto.EnvKeyProvider'
KMS_PROVIDER = 'accounts.crypto.KMSKeyProvider'

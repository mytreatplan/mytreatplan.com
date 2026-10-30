"""The OVHcloud KMS REST client: a re-export of `mytp_core.kms` (epic-platform entry 4,
architecture AD-10), where the code moved unchanged. Kept so existing imports work; to
replace a function in a test, patch `mytp_core.kms`, which the package's code calls."""
from mytp_core.kms import *  # noqa: F401,F403

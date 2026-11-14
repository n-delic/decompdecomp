"""Moved to `decomprepair.repair_tokens`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import repair_tokens

sys.modules[__name__] = repair_tokens

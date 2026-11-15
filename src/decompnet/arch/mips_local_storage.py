"""Moved to `decomprepair.mips_local_storage`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import mips_local_storage

sys.modules[__name__] = mips_local_storage

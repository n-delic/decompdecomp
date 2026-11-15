"""Moved to `decomprepair.mips_return_paths`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import mips_return_paths

sys.modules[__name__] = mips_return_paths

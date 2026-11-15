"""Moved to `decomprepair.mips_tail_dispatch`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import mips_tail_dispatch

sys.modules[__name__] = mips_tail_dispatch

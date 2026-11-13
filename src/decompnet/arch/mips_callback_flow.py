"""Moved to `decomprepair.mips_callback_flow`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import mips_callback_flow

sys.modules[__name__] = mips_callback_flow

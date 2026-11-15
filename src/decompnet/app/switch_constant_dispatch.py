"""Moved to `decomprepair.switch_constant_dispatch`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import switch_constant_dispatch

sys.modules[__name__] = switch_constant_dispatch

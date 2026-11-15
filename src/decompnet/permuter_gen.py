"""Moved to `decomprepair.permuter_gen`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import permuter_gen

sys.modules[__name__] = permuter_gen

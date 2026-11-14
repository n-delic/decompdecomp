"""Moved to `decomprepair.permuter_initialization`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import permuter_initialization

sys.modules[__name__] = permuter_initialization

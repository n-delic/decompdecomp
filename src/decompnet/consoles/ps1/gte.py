"""Moved to `decomprepair.ps1_gte`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import ps1_gte

sys.modules[__name__] = ps1_gte

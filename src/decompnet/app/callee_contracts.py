"""Moved to `decomprepair.callee_contracts`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import callee_contracts

sys.modules[__name__] = callee_contracts

"""Moved to `decomprepair.call_evidence`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import call_evidence

sys.modules[__name__] = call_evidence

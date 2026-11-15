"""Moved to `decompassets.n64.resource_inspection`, the standalone asset library
(RetroReversing/decomp-assets); this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decompassets.n64 import resource_inspection

sys.modules[__name__] = resource_inspection

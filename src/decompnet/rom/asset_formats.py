"""Moved to `decompassets.formats`, the standalone asset library
(RetroReversing/decomp-assets); this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decompassets import formats

sys.modules[__name__] = formats

"""Moved to `decompassets.n64.texture_packs`, the standalone asset library
(RetroReversing/decomp-assets); this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decompassets.n64 import texture_packs

sys.modules[__name__] = texture_packs

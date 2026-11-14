"""Moved to `decompassets.n64.model_links`, the standalone asset library
(RetroReversing/decomp-assets); this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decompassets.n64 import model_links

sys.modules[__name__] = model_links

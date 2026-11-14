"""Moved to `decompassets.n64.native_scene`, the standalone asset library
(RetroReversing/decomp-assets); this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decompassets.n64 import native_scene

sys.modules[__name__] = native_scene

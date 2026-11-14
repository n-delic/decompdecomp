"""Moved to `decomprepair.loop_storage_shapes`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import loop_storage_shapes

sys.modules[__name__] = loop_storage_shapes

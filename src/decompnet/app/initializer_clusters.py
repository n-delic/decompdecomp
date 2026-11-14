"""Moved to `decomprepair.initializer_clusters`, the standalone repair library; this name stays for the rest of the app
(both names are one module, so a patch through either is seen by both)."""

import sys

from decomprepair import initializer_clusters

sys.modules[__name__] = initializer_clusters

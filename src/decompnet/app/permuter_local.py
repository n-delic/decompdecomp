"""Compatibility name for the shared rewrite library; callers keep their existing imports."""

import sys

from decomprepair import permuter_local

sys.modules[__name__] = permuter_local

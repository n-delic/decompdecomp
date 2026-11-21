"""Compatibility name for the shared rewrite library; callers keep their existing imports."""

import sys

from decomprepair import permuter

sys.modules[__name__] = permuter

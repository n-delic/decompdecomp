"""Build the optional macOS RT64 adapter from pinned public sources.

The build itself is `decompnet.consoles.n64.rt64_build`, which the app also runs in the background at start
(`app/rt64_autobuild.py`); this script is the same build by hand."""
import sys

from decompnet.consoles.n64 import rt64_build

if __name__ == "__main__":
    sys.exit(rt64_build.main())

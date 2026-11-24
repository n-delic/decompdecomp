"""n64_frame_scan.py FOLDER: look through the frames a port saved for the ones that look wrong, without an emulator.

The scan itself is `decompnet.port.frame_scan` (the Porting tab's Scan for rendering faults runs it too); this is its
command line. See that module for what it finds and how to save the frames (PORT_FRAMES, PORT_SWAP_LOG)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from decompnet.port.frame_scan import *  # noqa: E402,F401,F403
from decompnet.port.frame_scan import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

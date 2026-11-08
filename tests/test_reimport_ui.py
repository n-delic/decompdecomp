"""Stored-source reimport and navigation use the same progress/error/Stop path."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_reimport_reuses_the_saved_game_and_reattaches_progress():
    if not shutil.which('node'):pytest.skip('needs node for GUI state checks')
    subprocess.run(['node','tests/gui_reimport_stored.js'],cwd=Path(__file__).resolve().parents[1],check=True,timeout=30)

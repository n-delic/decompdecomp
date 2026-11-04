import shutil
import subprocess
from pathlib import Path

import pytest


def test_assembly_controls_progress_errors_and_distinct_status_counters():
    node=shutil.which('node')
    if node is None:pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_source_assembly.js'))],check=True,capture_output=True,text=True,timeout=60)

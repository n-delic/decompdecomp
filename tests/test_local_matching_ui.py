"""The upstream Local Matching tab uses one script and the shared drawers/jobs."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_local_matching_drawers_and_job_lifecycle():
    node=shutil.which('node')
    if node is None:
        pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_local_matching.js'))],check=True,
                   capture_output=True,text=True,timeout=60)

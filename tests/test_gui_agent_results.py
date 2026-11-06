import shutil
import subprocess
from pathlib import Path

import pytest


def test_agent_result_dialog():
    node=shutil.which('node')
    if not node:
        pytest.skip('needs Node for GUI regression')
    subprocess.run([node,str(Path(__file__).with_name('gui_agent_results.js'))],check=True,capture_output=True,text=True,timeout=30)

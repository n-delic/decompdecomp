import shutil
import subprocess
from pathlib import Path

import pytest


def test_tool_settings_sort_save_and_error_feedback():
    node = shutil.which('node')
    if not node:
        pytest.skip('needs node')
    subprocess.run([node, str(Path(__file__).with_name('gui_tool_settings.js'))], check=True, timeout=30)

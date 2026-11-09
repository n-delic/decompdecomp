import shutil
import subprocess
from pathlib import Path

import pytest


def test_prompt_editor_inheritance_save_reset_and_errors():
    node = shutil.which('node')
    if not node:
        pytest.skip('needs node')
    subprocess.run([node, str(Path(__file__).with_name('gui_prompt_settings.js'))], check=True, timeout=30)

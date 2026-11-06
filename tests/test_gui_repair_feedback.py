"""Repair feedback UI tests live with the feature, outside the shared GUI test file."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_repair_feedback_rendering_review_and_local_exports():
    node = shutil.which('node')
    if not node:
        pytest.skip('needs node for the repair feedback UI')
    subprocess.run([node, str(Path(__file__).with_name('gui_repair_feedback.js'))],
                   check=True, capture_output=True, text=True, timeout=60)


def test_batch_candidate_recovery_controls():
    node = shutil.which('node')
    if not node:
        pytest.skip('needs node')
    subprocess.run([node, str(Path(__file__).with_name('gui_candidate_recovery.js'))], check=True,
                   capture_output=True, text=True, timeout=30)


def test_automatic_repair_replay_controls_and_cancellation():
    node=shutil.which('node')
    if not node:pytest.skip('needs node')
    subprocess.run([node,str(Path(__file__).with_name('gui_repair_replay.js'))],check=True,
                   capture_output=True,text=True,timeout=30)

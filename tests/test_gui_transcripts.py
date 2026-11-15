"""Transcript UI regressions stay with the feature rather than the shared GUI test file."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_transcript_full_text_usage_and_session_paging():
    node = shutil.which('node')
    if node is None:
        pytest.skip('needs node to run the transcript UI')
    subprocess.run([node, str(Path(__file__).with_name('gui_transcripts.js'))], check=True,
                   capture_output=True, text=True, timeout=60)


def test_page_loads_the_separate_transcript_assets():
    static = Path(__file__).resolve().parents[1] / 'src/decompnet/app/static'
    page = (static / 'index.html').read_text()
    assert '/assets/transcripts.css' in page
    assert '/assets/js/transcripts.js' in page
    assert page.index('/assets/js/agents.js') < page.index('/assets/js/transcripts.js')
    assert '.transcript-dialog' in (static / 'transcripts.css').read_text()

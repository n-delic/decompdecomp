"""decompdecomp's glue for N64 ports on libultraship (consoles/n64/port_glue): the game's threads and message
queues as libultra runs them, driven by a C program (tests/port_glue_threads.c) built with the computer's C compiler
(skips without one), and under ThreadSanitizer where the compiler has it."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from decompnet.consoles.n64.port import GLUE, GLUE_PROVIDES, LIBULTRASHIP, _sdk_group

DRIVER = Path(__file__).with_name("port_glue_threads.c")
CC = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")


def build(tmp_path, *flags):
    if CC is None:
        pytest.skip("No C compiler on this computer.")
    out = tmp_path / "glue_test"
    done = subprocess.run([CC, "-std=c11", "-O1", "-pthread", *flags, "-o", str(out), str(DRIVER),
                           str(GLUE / "threads.c")], capture_output=True, text=True)
    if done.returncode and flags:
        pytest.skip(f"This compiler can't build with {' '.join(flags)}: {done.stderr[-300:]}")
    assert done.returncode == 0, done.stderr
    return out


def test_threads_wait_and_run_by_priority_as_on_the_console(tmp_path):
    run = subprocess.run([str(build(tmp_path))], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stdout.strip() == "ok", run.stdout + run.stderr


def test_no_data_races(tmp_path):
    run = subprocess.run([str(build(tmp_path, "-g", "-fsanitize=thread"))], capture_output=True, text=True, timeout=120)
    assert run.stdout.strip() == "ok" and "WARNING: ThreadSanitizer" not in run.stderr, run.stderr[-2000:]


def test_the_kit_counts_what_the_glue_defines():
    """Every libultra function the glue defines is one the kit says the port has; the message queues it replaces
    are libultraship's names, so a link takes the glue's."""
    text = (GLUE / "threads.c").read_text(encoding="utf-8")
    defined = set(re.findall(r"^[A-Za-z_][\w \*]*?\b((?:__)?os[A-Z]\w*)\s*\([^;]*?\)\s*\{", text, re.M))
    assert defined - GLUE_PROVIDES == {"osCreateMesgQueue", "osSendMesg", "osJamMesg", "osRecvMesg",
                                       "osSetEventMesg"} <= LIBULTRASHIP
    assert {_sdk_group(n) for n in GLUE_PROVIDES} == {"replaced"}
    assert "__osEventStateTab[OS_NUM_EVENTS] = {" in text  # a definition, not a tentative one (see the file)

"""Wait for a helper's prompt before starting its CLI, so agents.py can attach a Windows job first.

Attaching a job to an already running CLI races with children it starts during initialization. This small gate
cannot start children before the parent has assigned it to a job and sent the prompt. Output stays on the original
pipes; this wrapper never interprets the agent's command line or its prompt.
"""

import subprocess
import sys


def main() -> int:
    prompt = sys.stdin.buffer.read()
    if not prompt.startswith(b"\x01"):  # the app died before it could attach the job and open the gate
        return 1
    proc = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    proc.communicate(input=prompt[1:])
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())

"""Actionable host storage failures, kept separate from source and compiler verdicts."""
import errno
import os
from pathlib import Path


def message(error):
    code = getattr(error, 'errno', None)
    if code in (errno.EMFILE, errno.ENFILE):
        return 'Too many files are open. Stop some background work or increase the open-file limit, then retry.'
    quota = getattr(errno, 'EDQUOT', None)
    if code != errno.ENOSPC and (quota is None or code != quota):
        return None
    filename = getattr(error, 'filename', None)
    location = f' at {Path(os.fsdecode(filename)).parent}' if filename else ''
    if code == errno.ENOSPC:
        return f'Not enough disk space{location}. Free space on that drive, then retry.'
    return f'The storage quota was reached{location}. Free space or increase the quota, then retry.'

"""Extract the pinned MIPSpro C++ frontend from an IRIX installation image."""

import hashlib
import re
import subprocess
from pathlib import Path

from ... import limits


def extract(root: Path) -> None:
    """Validate both the image entry and the extracted executable before publishing it."""
    name = 'usr/lib/DCC/edgcpfe'
    record = next((line for line in (root / 'c++_fe.idb').read_text(encoding='latin1').splitlines()
                   if len(line.split()) > 4 and line.split()[0] == 'f' and line.split()[4] == name), None)
    if record is None:
        raise ValueError('The IRIX image does not contain the MIPSpro frontend.')
    size = re.search(r'\bsize\((\d+)\)', record)
    compressed = re.search(r'\bcmpsize\((\d+)\)', record)
    if not size or not compressed:
        raise ValueError('The MIPSpro frontend image has an invalid size record.')
    expected, count = int(size[1]), int(compressed[1])
    image = (root / 'c++_fe.sw').read_bytes()
    marker = b'\0' + bytes([len(name)]) + name.encode()
    offset = image.find(marker)
    if offset < 0:
        raise ValueError('The MIPSpro frontend is missing from the IRIX image.')
    blob = image[offset + len(marker):offset + len(marker) + (count or expected)]
    if count:
        blob = subprocess.run(limits.capped(['gzip', '-dc']), input=blob, capture_output=True, check=True,
                              timeout=60).stdout
    if len(blob) != expected or hashlib.sha256(blob).hexdigest() != '459cc16e72de63fb17137cd7dc02f4bc211044e40e0956a92e81eeda47e1d9a3':
        raise ValueError('The extracted MIPSpro frontend did not match its pinned checksum.')
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blob)
    target.chmod(0o755)

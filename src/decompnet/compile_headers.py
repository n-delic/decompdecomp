"""Synchronize private compiler headers by exact bytes without rebuilding untouched files."""
import os
import shutil
from pathlib import Path


def read(roots):
    files={}
    for directory in roots:
        root=Path(directory)
        if not root.exists():raise FileNotFoundError(root)
        if not root.is_dir():raise NotADirectoryError(root)
        pending=[root]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    path=Path(entry.path)
                    if entry.is_dir():
                        if entry.is_symlink():return None
                        pending.append(path)
                    else:
                        if not entry.is_file():raise shutil.SpecialFileError(str(path))
                        data=path.read_bytes()
                        if path.suffix.lower() in ('.h','.hpp','.hxx','.inc','.inl'):
                            data=data.replace(b'\r\n',b'\n')
                        files[str(path.relative_to(root))]=data
    return files


def stage(inc,files):
    if inc.is_symlink() or inc.exists() and not inc.is_dir():inc.unlink()
    inc.mkdir(exist_ok=True)
    directories={str(parent) for name in files for parent in Path(name).parents if str(parent)!='.'}
    missing=dict(files)
    pending=[inc]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path=Path(entry.path);name=str(path.relative_to(inc))
                if entry.is_symlink():
                    path.unlink()
                elif entry.is_dir(follow_symlinks=False):
                    if name in directories:pending.append(path)
                    else:shutil.rmtree(path)
                elif name not in files or not entry.is_file(follow_symlinks=False):
                    path.unlink()
                else:
                    data=missing.pop(name)
                    if path.read_bytes()!=data:path.write_bytes(data)
    for name,data in missing.items():
        path=inc/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)

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


def stage(inc,files,known=None):
    """Make `inc` hold exactly `files`. `known` (name -> (stamp, bytes)) remembers what this process last wrote
    there: a file whose size, modification, change time and inode still match and whose bytes are the ones wanted is not
    read back. Only this private copy is trusted that way; the project's own headers are always read (read())."""
    if inc.is_symlink() or inc.exists() and not inc.is_dir():inc.unlink()
    inc.mkdir(exist_ok=True)
    directories={str(parent) for name in files for parent in Path(name).parents if str(parent)!='.'}
    missing=dict(files)
    written={}
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
                    stat=entry.stat(follow_symlinks=False)
                    stamp=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_ino)
                    kept=(known or {}).get(name)
                    if kept is not None and kept[0]==stamp and kept[1]==data:
                        written[name]=kept;continue
                    if path.read_bytes()!=data:
                        path.write_bytes(data);stat=path.stat();stamp=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_ino)
                    written[name]=(stamp,data)
    for name,data in missing.items():
        path=inc/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        stat=path.stat();written[name]=((stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_ino),data)
    return written

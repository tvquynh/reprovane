"""OS-held workspace writer lock. File presence is diagnostic, never proof of ownership."""
import os,json
from pathlib import Path
from contextlib import contextmanager
from ..core import ValidationError


@contextmanager
def workspace_lock(workspace):
    root=Path(workspace)
    if root.is_symlink():raise ValidationError('Workspace symlink refused')
    root.mkdir(parents=True,exist_ok=True);path=root/'.litintel-writer.lock'
    if path.is_symlink():raise ValidationError('Lock symlink refused')
    handle=path.open('a+b')
    try:
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise ValidationError('Workspace writer is active') from None
        handle.seek(0);handle.truncate();handle.write(json.dumps(dict(schema='writer-lock/1',pid=os.getpid(),state='OS_LOCK_OWNS_LIFETIME')).encode());handle.flush()
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
    finally:handle.close()


def lock_diagnosis(root):
    path=Path(root)/'.litintel-writer.lock'
    if not path.exists():return 'NO_LOCK_METADATA'
    if path.is_symlink():return 'UNSAFE_LOCK_PATH'
    try:
        with path.open('r+b') as f:
            f.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(f.fileno(),fcntl.LOCK_UN)
        return 'INACTIVE_METADATA_OS_LOCK_RELEASED'
    except OSError:return 'ACTIVE_WRITER_OR_INACCESSIBLE'

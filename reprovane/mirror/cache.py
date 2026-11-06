"""Filesystem transport state. Copies isolate snapshot bytes from CAS corruption."""
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from .models import repository,commit,sha256,safe_path,MirrorCacheError,MirrorIntegrityError,MirrorStateError


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()


def fsync_directory(path):
    try:
        fd=os.open(path,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except OSError: pass  # Directory fsync is unavailable on some Windows/filesystem combinations.


class MirrorCache:
    def __init__(self,workspace):
        self.workspace=Path(workspace).absolute()
        self.root=self.workspace/'mirror'
        self._safe(self.root)
        self.root.mkdir(parents=True,exist_ok=True)

    def _safe(self,path):
        path=Path(path).absolute()
        if not path.is_relative_to(self.workspace): raise MirrorCacheError('Path outside mirror workspace')
        for item in [path,*path.parents]:
            if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()): raise MirrorCacheError('Symlink/junction cache path rejected')
        return path

    def _parent(self,path):
        self._safe(path); path.parent.mkdir(parents=True,exist_ok=True); self._safe(path)
        return path

    def object_path(self,digest):
        digest=sha256(digest)
        return self._safe(self.root/'objects'/'sha256'/digest[:2]/digest)

    def verify_object(self,digest):
        path=self.object_path(digest)
        return path.is_file() and file_hash(path)==digest

    @contextmanager
    def temporary_object(self):
        directory=self._parent(self.root/'objects'/'incoming'/'placeholder').parent
        fd,name=tempfile.mkstemp(prefix='.object-',dir=directory); os.close(fd)
        path=Path(name)
        try: yield path
        finally: path.unlink(missing_ok=True)

    def promote_object(self,temporary,expected):
        expected=sha256(expected); temporary=self._safe(temporary)
        if file_hash(temporary)!=expected: raise MirrorIntegrityError('Downloaded object SHA-256 mismatch')
        target=self._parent(self.object_path(expected))
        if self.verify_object(expected): return target
        # Recovery only after proving the incoming bytes match this exact hash key.
        os.replace(temporary,target); fsync_directory(target.parent)
        return target

    def put_bytes(self,data):
        expected=hashlib.sha256(data).hexdigest()
        if self.verify_object(expected): return expected
        with self.temporary_object() as temporary:
            with temporary.open('wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
            self.promote_object(temporary,expected)
        return expected

    def materialize_file(self,digest,destination):
        target=self._parent(Path(destination))
        if target.exists(): raise MirrorCacheError('Refusing to overwrite materialized file')
        if not self.verify_object(digest): raise MirrorIntegrityError('Cached object missing or corrupt')
        shutil.copyfile(self.object_path(digest),target)
        with target.open('r+b') as stream: stream.flush(); os.fsync(stream.fileno())
        if file_hash(target)!=digest: raise MirrorIntegrityError('Materialized object SHA-256 mismatch')

    def _namespace(self,repo,path):
        repo=repository(repo); path=safe_path(path)
        return repo.split('/'),hashlib.sha256(path.encode('utf-8')).hexdigest()

    def snapshot_path(self,repo,sha,path):
        parts,key=self._namespace(repo,path)
        return self._safe(self.root/'snapshots'/'github'/parts[0]/parts[1]/commit(sha)/key)

    def state_path(self,repo,path):
        parts,key=self._namespace(repo,path)
        return self._safe(self.root/'state'/'github'/parts[0]/parts[1]/(key+'.json'))

    def read_json(self,path):
        path=self._safe(path)
        try:
            if path.stat().st_size>4*1024*1024: raise MirrorStateError('Mirror metadata exceeds limit')
            value=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value,dict): raise ValueError()
            return value
        except (OSError,ValueError,UnicodeError): raise MirrorStateError('Missing or invalid mirror metadata') from None

    def atomic_json(self,path,data):
        path=self._parent(Path(path)); temporary=None
        try:
            fd,name=tempfile.mkstemp(prefix='.json-',dir=path.parent); temporary=Path(name)
            with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
                json.dump(data,stream,sort_keys=True,separators=(',',':'),ensure_ascii=False); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,path); fsync_directory(path.parent)
        except OSError: raise MirrorStateError('Atomic mirror metadata write failed') from None
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)

    @contextmanager
    def staging_snapshot(self,final):
        final=self._parent(final)
        path=Path(tempfile.mkdtemp(prefix='.snapshot-',dir=final.parent))
        try: yield path
        finally:
            if path.exists(): self.remove_owned_staging(path)

    def remove_owned_staging(self,path):
        path=self._safe(path)
        if not path.is_relative_to(self.root/'snapshots') or path==self.root/'snapshots': raise MirrorCacheError('Unsafe snapshot cleanup')
        # No user path is interpolated into a shell; symlink children cannot be followed by rmtree.
        shutil.rmtree(path)

    def promote_snapshot(self,staging,final):
        self._safe(staging); self._safe(final)
        if final.exists(): raise MirrorCacheError('Completed snapshot already exists')
        os.rename(staging,final); fsync_directory(final.parent)

    @contextmanager
    def sync_lock(self,repo,path):
        lock=self._parent(self.state_path(repo,path).with_suffix('.lock'))
        try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError: raise MirrorStateError('Mirror sync already locked; inspect stale lock manually') from None
        try:
            os.close(fd); yield
        finally: lock.unlink(missing_ok=True)

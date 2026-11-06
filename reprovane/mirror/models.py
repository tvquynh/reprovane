"""Transport models, narrow safe errors and input boundaries; no credentials."""
import re
from dataclasses import dataclass,asdict
from datetime import datetime,timezone


class MirrorSyncError(Exception): pass
class GitHubResolutionError(MirrorSyncError): pass
class GitHubFetchError(MirrorSyncError): pass
class MirrorIntegrityError(MirrorSyncError): pass
class MirrorOfflineError(MirrorSyncError): pass
class MirrorCacheError(MirrorSyncError): pass
class MirrorStateError(MirrorSyncError): pass


def utc_now(): return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def repository(value):
    if not isinstance(value,str): raise MirrorSyncError('Invalid GitHub repository')
    value=value.removeprefix('https://github.com/').rstrip('/')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',value):
        raise MirrorSyncError('Expected GitHub owner/repository')
    for part in value.split('/'): safe_path(part)
    return value.lower()


def safe_path(value):
    if not isinstance(value,str) or not value or len(value)>1024 or any(c in value for c in '\\:%?#\x00'):
        raise MirrorSyncError('Unsafe repository path')
    for part in value.split('/'):
        if not re.fullmatch(r'[A-Za-z0-9_. -]+',part) or part in ('.','..') or part.endswith((' ','.')) or '..' in part:
            raise MirrorSyncError('Unsafe repository path')
        if part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
            raise MirrorSyncError('Reserved repository path')
    return value


def ref(value):
    if not isinstance(value,str) or len(value)>256 or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]*',value) or any(p in ('','.','..') or p.startswith('.') or p.endswith(('.','.lock')) for p in value.split('/')) or '..' in value:
        raise MirrorSyncError('Unsafe requested Git ref')
    return value


def commit(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-fA-F]{40}',value): raise MirrorSyncError('Expected full 40-hex Git commit')
    return value.lower()


def sha256(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value): raise MirrorIntegrityError('Expected lowercase SHA-256')
    return value


@dataclass(frozen=True)
class ResolvedGitHubRef:
    repository: str
    requested_ref: str
    resolved_commit_sha: str
    resolved_at_utc: str


@dataclass(frozen=True)
class SyncResult:
    repository: str
    requested_ref: str
    resolved_commit_sha: str
    previous_commit_sha: str | None
    status: str
    snapshot_directory: str
    repository_path: str
    manifest_schema_version: int
    available_sjr_years: tuple
    latest_snapshot_year: int
    journal_value_years: tuple
    downloaded_files: tuple
    reused_files: tuple
    downloaded_bytes: int
    manifest_sha256: str
    checksums_sha256: str
    verified: bool = True

    def as_dict(self): return asdict(self)

"""Finite-file plan adapter over Stage05 transport/CAS/promotion/state primitives.

For sources without published checksums, SHA-256 is computed from HTTPS bytes at
one exact Git commit. It is not presented as an upstream-published checksum.
"""
import hashlib
from pathlib import Path
from .github import GitHubClient
from .cache import MirrorCache,file_hash
from .models import repository,ref,commit,safe_path,utc_now,MirrorIntegrityError,MirrorOfflineError,MirrorSyncError
from .scimago_sync import _state,_is_commit
from ..core.provenance import canonical_json


def _verify_pinned_files(cache,root,repo=None,sha=None,paths=None):
    root=cache._safe(Path(root)); side=cache.read_json(root/'sync_provenance.json')
    if side.get('profile')!='pinned_files_v1' or side.get('verified') is not True: raise MirrorIntegrityError('Invalid pinned file provenance')
    actual_repo=repository(side['repository']); actual_sha=commit(side['resolved_commit_sha']); ref(side['requested_ref'])
    if repo is not None and repo!=actual_repo or sha is not None and sha!=actual_sha: raise MirrorIntegrityError('Pinned source context mismatch')
    expected=side['files']
    if not isinstance(expected,dict) or not expected or paths is not None and sorted(expected)!=sorted(paths): raise MirrorIntegrityError('Pinned file plan mismatch')
    for name,digest in expected.items():
        path=cache._safe(root/safe_path(name))
        if not path.is_file() or file_hash(path)!=digest or not cache.verify_object(digest): raise MirrorIntegrityError('Pinned file/object missing or corrupt')
    return side


def verify_pinned_files(cache,root,repo=None,sha=None,paths=None):
    try: return _verify_pinned_files(cache,root,repo,sha,paths)
    except (KeyError,TypeError,ValueError,OSError): raise MirrorIntegrityError('Malformed pinned file metadata or unavailable bytes') from None


def sync_pinned_files(workspace,files,*,repository_name='tvquynh/wosaide-journal-lists',requested_ref='control',offline=False,client=None):
    """Reuse the Stage05 client/cache. No HTTP implementation or semantic parser here."""
    repo=repository(repository_name); requested=ref(requested_ref); paths=sorted({safe_path(p) for p in files})
    if not paths or len(paths)!=len(files) or 'sync_provenance.json' in paths: raise MirrorSyncError('Invalid finite file plan')
    namespace='files-'+hashlib.sha256(canonical_json(paths).encode()).hexdigest()
    cache=MirrorCache(workspace); state=_state(cache,repo,namespace)
    if offline:
        sha=_is_commit(requested)
        if sha is None:
            if state is None or state.get('requested_ref')!=requested: raise MirrorOfflineError('No local state for requested file plan/ref')
            sha=state['last_successful_commit_sha']
        root=cache.snapshot_path(repo,sha,namespace)
        try: side=verify_pinned_files(cache,root,repo,sha,paths)
        except (MirrorSyncError,OSError,KeyError,TypeError): raise MirrorOfflineError('No verified offline pinned file plan') from None
        return dict(**side,snapshot_directory=str(root),status='OFFLINE_REUSED')
    owned=client is None; client=client or GitHubClient()
    try:
        with cache.sync_lock(repo,namespace):
            resolved=client.resolve_ref(repo,requested); sha=commit(resolved.resolved_commit_sha)
            if repository(resolved.repository)!=repo or resolved.requested_ref!=requested: raise MirrorIntegrityError('Resolved context mismatch')
            root=cache.snapshot_path(repo,sha,namespace); promoted=False
            if root.exists():
                side=verify_pinned_files(cache,root,repo,sha,paths); status='UP_TO_DATE'
            else:
                hashes={}; sizes={}
                # No upstream manifest/checksum exists for these lists: fetch once per new commit.
                for path in paths:
                    with cache.temporary_object() as temporary:
                        digest,size=client.fetch_to_file(repo,sha,path,temporary)
                        cache.promote_object(temporary,digest)
                    hashes[path]=digest; sizes[path]=size
                side=dict(schema_version=1,profile='pinned_files_v1',repository=repo,requested_ref=requested,resolved_commit_sha=sha,
                    resolved_at_utc=resolved.resolved_at_utc,synced_at_utc=utc_now(),files=hashes,byte_sizes=sizes,verified=True,
                    integrity_basis='exact Git commit via HTTPS; locally computed SHA-256, no published checksum asserted')
                with cache.staging_snapshot(root) as staging:
                    for path,digest in hashes.items(): cache.materialize_file(digest,staging/path)
                    cache.atomic_json(staging/'sync_provenance.json',side)
                    verify_pinned_files(cache,staging,repo,sha,paths)
                    cache.promote_snapshot(staging,root); promoted=True
                status='UPDATED'
            try:
                cache.atomic_json(cache.state_path(repo,namespace),dict(schema_version=1,convenience_metadata_only=True,
                    last_successful_commit_sha=sha,requested_ref=requested,snapshot_path=str(root),updated_at_utc=utc_now()))
            except BaseException:
                if promoted: cache.remove_owned_staging(root)
                raise
            return dict(**side,snapshot_directory=str(root),status=status)
    finally:
        if owned: client.close()

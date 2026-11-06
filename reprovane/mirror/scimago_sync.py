"""Resolve once, plan from shared Stage04 parsers, verify, promote, then state."""
from pathlib import Path
from ..core import ValidationError,ArtifactIntegrityError
from ..providers.scimago import (REPOSITORY,BRANCH,parse_scimago_manifest_bytes,
    parse_scimago_checksums_bytes,declared_scimago_files,validate_local_package,
    import_scimago_source_snapshot,import_scimago_apc_snapshot)
from .models import (repository,ref,commit,safe_path,utc_now,SyncResult,MirrorIntegrityError,
    MirrorOfflineError,MirrorStateError,MirrorSyncError)
from .github import GitHubClient
from .cache import MirrorCache


def _is_commit(value):
    try: return commit(value)
    except MirrorSyncError: return None


def _state(cache,repo,path):
    target=cache.state_path(repo,path)
    if not target.exists(): return None
    try:
        state=cache.read_json(target); sha=commit(state['last_successful_commit_sha'])
        if state['snapshot_path']!=str(cache.snapshot_path(repo,sha,path)): return None
        return state
    except (MirrorSyncError,KeyError,TypeError): return None


def _validate_snapshot(cache,final,repo,sha,path,*,verify_objects=True):
    cache._safe(final)
    if not final.is_dir(): raise MirrorIntegrityError('Pinned snapshot is missing')
    try:
        side=cache.read_json(final/'sync_provenance.json')
        if side['schema_version']!=1 or side['repository']!=repo or side['repository_path']!=path or side['resolved_commit_sha']!=sha or side['verified'] is not True:
            raise MirrorIntegrityError('Pinned snapshot provenance mismatch')
        pkg=final/path
        # Reject even within-tree symlinks, rather than trusting arbitrary links.
        cache._safe(pkg)
        for item in pkg.rglob('*'): cache._safe(item)
        package=validate_local_package(pkg)
        if side['manifest_sha256']!=package.manifest_sha256 or side['checksums_sha256']!=package.checksums_sha256:
            raise MirrorIntegrityError('Pinned control metadata mismatch')
        declared=declared_scimago_files(dict(source_files=package.source_files,apc_files=package.apc_files),package.checksums)
        if side['declared_data_files']!=declared: raise MirrorIntegrityError('Pinned file plan mismatch')
        if side['manifest_schema_version']!=package.manifest['schema_version'] or side['available_sjr_years']!=list(package.years) or side['latest_snapshot_year']!=package.latest_year or side['journal_value_years']!=sorted(package.apc_files):
            raise MirrorIntegrityError('Pinned annual metadata mismatch')
        if verify_objects:
            for digest in [package.manifest_sha256,package.checksums_sha256,*declared.values()]:
                if not cache.verify_object(digest): raise MirrorIntegrityError('Pinned object missing or corrupt')
        return package,side
    except (ValidationError,ArtifactIntegrityError,KeyError,TypeError,OSError):
        raise MirrorIntegrityError('Pinned snapshot validation failed') from None


def _result(repo,requested,sha,previous,status,final,path,package,downloaded=(),reused=None,downloaded_bytes=0):
    if reused is None: reused=sorted(list(package.source_files.values())+list(package.apc_files.values()))
    return SyncResult(repo,requested,sha,previous,status,str(final),path,package.manifest['schema_version'],tuple(package.years),
        package.latest_year,tuple(sorted(package.apc_files)),tuple(sorted(downloaded)),tuple(sorted(reused)),downloaded_bytes,
        package.manifest_sha256,package.checksums_sha256)


def _write_state(cache,repo,requested,sha,path,final,package):
    cache.atomic_json(cache.state_path(repo,path),dict(schema_version=1,convenience_metadata_only=True,
        last_successful_commit_sha=sha,requested_ref=requested,snapshot_path=str(final),updated_at_utc=utc_now(),
        manifest_sha256=package.manifest_sha256,checksums_sha256=package.checksums_sha256))


def sync_scimago_from_github(workspace,*,repository_name=REPOSITORY,requested_ref=BRANCH,
                             repository_path='SCIMAGO',offline=False,client=None):
    repo=repository(repository_name); requested=ref(requested_ref); path=safe_path(repository_path)
    if type(offline) is not bool: raise MirrorSyncError('offline must be boolean')
    cache=MirrorCache(workspace); state=_state(cache,repo,path)
    previous=state['last_successful_commit_sha'] if state else None
    if offline:
        sha=_is_commit(requested)
        if sha is None:
            if state is None or state.get('requested_ref')!=requested: raise MirrorOfflineError('No successful local state for requested ref')
            sha=previous
        final=cache.snapshot_path(repo,sha,path)
        try: package,_=_validate_snapshot(cache,final,repo,sha,path)
        except MirrorSyncError: raise MirrorOfflineError('No verified local pinned snapshot available offline') from None
        return _result(repo,requested,sha,previous,'OFFLINE_REUSED',final,path,package)
    owned=client is None; client=client or GitHubClient()
    try:
        with cache.sync_lock(repo,path):
            resolved=client.resolve_ref(repo,requested); sha=commit(resolved.resolved_commit_sha)
            if repository(resolved.repository)!=repo or resolved.requested_ref!=requested: raise MirrorIntegrityError('Resolved ref context mismatch')
            final=cache.snapshot_path(repo,sha,path)
            if final.exists():
                # Completed snapshots are never changed. Corruption fails closed, including online.
                package,_=_validate_snapshot(cache,final,repo,sha,path,verify_objects=False)
                # Repair transport objects without changing the completed snapshot.
                for name,digest in [('manifest.json',package.manifest_sha256),(package.checksums_filename,package.checksums_sha256)]:
                    if not cache.verify_object(digest): cache.put_bytes((package.root/name).read_bytes())
                recovered=[]; reused=[]; size_total=0
                for name,expected in sorted(declared_scimago_files(dict(source_files=package.source_files,apc_files=package.apc_files),package.checksums).items()):
                    if cache.verify_object(expected): reused.append(name)
                    else:
                        actual,size=client.fetch_to_object_cache(cache,repo,sha,path+'/'+name,expected)
                        if actual!=expected or not cache.verify_object(expected): raise MirrorIntegrityError('Recovered data integrity failed')
                        recovered.append(name); size_total+=size
                _write_state(cache,repo,requested,sha,path,final,package)
                return _result(repo,requested,sha,previous,'UPDATED' if recovered else 'UP_TO_DATE',final,path,package,recovered,reused,size_total)
            manifest=client.fetch_bytes_at_commit(repo,sha,path+'/manifest.json',control=True)
            try: plan=parse_scimago_manifest_bytes(manifest)
            except ValidationError: raise MirrorIntegrityError('Invalid SCImago manifest plan') from None
            # All future local filenames must also satisfy the mirror's cross-platform safety boundary.
            for name in [plan['checksums_filename'],*plan['source_files'].values(),*plan['apc_files'].values()]: safe_path(name)
            checksums=client.fetch_bytes_at_commit(repo,sha,path+'/'+plan['checksums_filename'],control=True)
            try: declared=declared_scimago_files(plan,parse_scimago_checksums_bytes(checksums))
            except ValidationError: raise MirrorIntegrityError('Invalid SCImago checksum plan') from None
            mh=cache.put_bytes(manifest); ch=cache.put_bytes(checksums)
            downloaded=[]; reused=[]; count=0
            for name,expected in sorted(declared.items()):
                if cache.verify_object(expected): reused.append(name)
                else:
                    actual,size=client.fetch_to_object_cache(cache,repo,sha,path+'/'+name,expected)
                    if actual!=expected or not cache.verify_object(expected): raise MirrorIntegrityError('Pinned data integrity failed')
                    downloaded.append(name); count+=size
            provenance=dict(schema_version=1,repository=repo,requested_ref=requested,resolved_commit_sha=sha,
                resolved_at_utc=resolved.resolved_at_utc,synced_at_utc=utc_now(),repository_path=path,
                manifest_sha256=mh,checksums_sha256=ch,manifest_schema_version=plan['manifest']['schema_version'],
                available_sjr_years=list(plan['years']),latest_snapshot_year=plan['latest_year'],journal_value_years=sorted(plan['apc_files']),
                declared_data_files=declared,downloaded_files=sorted(downloaded),reused_files=sorted(reused),downloaded_bytes=count,
                downloaded_bytes_scope='declared data files only, excluding controls and unsuccessful retries',verified=True)
            promoted=False
            try:
                with cache.staging_snapshot(final) as staging:
                    package_root=staging/path
                    for name,digest in sorted({'manifest.json':mh,plan['checksums_filename']:ch,**declared}.items()):
                        cache.materialize_file(digest,package_root/name)
                    try: package=validate_local_package(package_root)
                    except (ValidationError,ArtifactIntegrityError): raise MirrorIntegrityError('Staged SCImago package failed Stage04 validation') from None
                    cache.atomic_json(staging/'sync_provenance.json',provenance)
                    _validate_snapshot(cache,staging,repo,sha,path)
                    cache.promote_snapshot(staging,final); promoted=True
                    _write_state(cache,repo,requested,sha,path,final,package)
            except BaseException:
                if promoted: cache.remove_owned_staging(final)  # Only this call's just-promoted unreferenced snapshot.
                raise
            return _result(repo,requested,sha,previous,'UPDATED',final,path,package,downloaded,reused,count)
    except OSError: raise MirrorStateError('Mirror filesystem operation failed') from None
    finally:
        if owned: client.close()


def import_synced_scimago(database,result):
    """Explicit thin Stage04 composition. Sync never updates a scientific run automatically."""
    repo=repository(result.repository); sha=commit(result.resolved_commit_sha); path=safe_path(result.repository_path)
    if result.verified is not True: raise MirrorIntegrityError('Unverified sync result')
    root=Path(result.snapshot_directory)/path; package=validate_local_package(root)
    if package.manifest_sha256!=result.manifest_sha256 or package.checksums_sha256!=result.checksums_sha256:
        raise MirrorIntegrityError('Sync result no longer matches local package')
    for files,importer in [(package.source_files,import_scimago_source_snapshot),(package.apc_files,import_scimago_apc_snapshot)]:
        for year,name in sorted(files.items()):
            yield importer(database,root,year,distribution_repository=repo,distribution_branch_requested=ref(result.requested_ref),
                distribution_resolved_commit_sha=sha,distribution_repository_path=path+'/'+name)

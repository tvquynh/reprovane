"""Orphan detection for the content-addressed store. Nothing is ever deleted.

An orphan is a CAS object that no database table references, typically bytes placed just before an
interruption. Reprocessing the same download reuses such an object, so orphans are harmless. --quarantine
moves them (and leftover staging files) to a separate folder after re-verification under the writer lock.
"""
import hashlib
import os
import re
import shutil
from pathlib import Path

from ..core import ValidationError

REFERENCES = (('raw_artifact', 'artifact_sha256'), ('fulltext_artifact', 'artifact_sha256'),
              ('fulltext_evidence', 'response_sha256'), ('pdf_evidence', 'response_sha256'), ('pdf_artifact', 'artifact_sha256'))


def referenced(db):
    found = set()
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, column in REFERENCES:
        if table in tables:
            found |= {r[0] for r in db.execute(f'SELECT {column} FROM {table} WHERE {column} IS NOT NULL')}
    return found


def scan(db, workspace):
    root = Path(workspace) / 'raw' / 'sha256'
    known = referenced(db)
    orphans, staging, invalid = [], [], []
    if root.is_symlink():
        raise ValidationError('CAS symlink directory refused')
    for path in sorted(root.rglob('*')) if root.exists() else []:
        if path.is_symlink():
            invalid.append(path.relative_to(workspace).as_posix())
        elif path.is_file():
            rel = path.relative_to(workspace).as_posix()
            if path.name.startswith('.put-'):
                staging.append(dict(path=rel, byte_size=path.stat().st_size))
            elif re.fullmatch(r'[0-9a-f]{64}', path.name) and path.parent.name == path.name[:2]:
                if path.name not in known:
                    orphans.append(dict(path=rel, sha256=path.name, byte_size=path.stat().st_size))
            else:
                invalid.append(rel)
    return dict(referenced=len(known), orphans=orphans, staging_files=staging, invalid_entries=invalid)


def quarantine(db, workspace, destination):
    workspace, destination = Path(workspace).resolve(), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValidationError('Quarantine destination must be a new folder')
    if destination.resolve().is_relative_to(workspace / 'raw'):
        raise ValidationError('Quarantine destination must be outside the content-addressed store')
    report = scan(db, workspace)
    known = referenced(db)
    destination.mkdir(parents=True)
    moved = []
    for entry in report['orphans'] + report['staging_files']:
        source = workspace / entry['path']
        if entry.get('sha256'):
            if entry['sha256'] in known:
                continue
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            entry = dict(entry, content_matches_name=digest == entry['sha256'])
        target = destination / entry['path'].replace('/', '__')
        shutil.copy2(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValidationError('Quarantine copy verification failed')
        os.remove(source)
        moved.append(dict(entry, quarantined_as=target.name))
    return dict(report, quarantined=moved, destination_name=destination.name)

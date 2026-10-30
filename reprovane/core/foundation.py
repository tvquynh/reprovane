"""Small repository API: provider observations only, with no scholarly identity logic."""
import hashlib
import json
from contextlib import nullcontext
from typing import Any

from . import ArtifactIntegrityError, SnapshotSealedError, ValidationError
from .artifacts import ContentAddressedArtifactStore, StoredArtifact
from .db import Database
from .ids import new_id, utc_now
from .provenance import canonical_json, json_text, sanitize_locator

Row = dict[str, Any]


class Foundation:
    def __init__(self, database: Database):
        self.db = database
        self.artifacts = ContentAddressedArtifactStore(database.workspace)

    def _transaction(self):
        # Service methods compose inside a caller-owned transaction without nesting BEGIN.
        return nullcontext() if self.db.connection.in_transaction else self.db.transaction()

    @staticmethod
    def _text(*values: str) -> None:
        if any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValidationError("Required text is missing")
        try:
            for v in values:
                v.encode("utf-8")
        except UnicodeError:
            raise ValidationError("Text must be valid UTF-8") from None

    def _one(self, table: str, key: str, value: str) -> Row:
        row = self.db.execute(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).fetchone()
        if row is None:
            raise ValidationError("Requested foundation object does not exist")
        return dict(row)

    def _insert(self, table: str, row: Row) -> Row:
        with self._transaction():
            self.db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                            tuple(row.values()))
        return row.copy()

    def create_project(self, project_key: str, display_name: str) -> Row:
        self._text(project_key, display_name)
        return self._insert("review_project", dict(project_id=new_id("prj"), project_key=project_key,
                                                   display_name=display_name, created_at_utc=utc_now()))

    def get_project(self, project_id: str) -> Row:
        return self._one("review_project", "project_id", project_id)

    def create_project_revision(self, project_id: str, revision_number: int, manifest_format: str,
                                manifest_text: str, *, parent_revision_id: str | None = None) -> Row:
        self._text(project_id, manifest_format)
        if not isinstance(manifest_text, str) or type(revision_number) is not int or revision_number < 1:
            raise ValidationError("Invalid project revision")
        try:
            digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
        except UnicodeError:
            raise ValidationError("Manifest must be valid UTF-8 text") from None
        return self._insert("project_revision", dict(revision_id=new_id("rev"), project_id=project_id,
            revision_number=revision_number, parent_revision_id=parent_revision_id, manifest_format=manifest_format,
            manifest_text=manifest_text, manifest_sha256=digest, created_at_utc=utc_now()))

    def get_project_revision(self, revision_id: str) -> Row:
        return self._one("project_revision", "revision_id", revision_id)

    def list_project_revisions(self, project_id: str) -> list[Row]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM project_revision WHERE project_id = ? ORDER BY revision_number", (project_id,))]

    def register_provider(self, semantic_namespace: str, display_name: str, *,
                          upstream_authority: str | None = None) -> Row:
        self._text(semantic_namespace, display_name)
        return self._insert("provider", dict(provider_id=new_id("prov"), semantic_namespace=semantic_namespace,
            display_name=display_name, upstream_authority=upstream_authority, created_at_utc=utc_now()))

    def get_provider(self, provider_id: str) -> Row:
        return self._one("provider", "provider_id", provider_id)

    def create_provider_snapshot(self, provider_id: str, dataset: str, acquisition_mode: str, *,
            parser_name: str, parser_version: str, schema_profile: str, schema_version: str,
            upstream_release: str | None = None, source_locator: str | None = None,
            request_spec_sha256: str | None = None, mirror_commit_sha: str | None = None,
            license_note: str | None = None, provenance_note: str | None = None,
            metadata_json: str = "{}") -> Row:
        self._text(provider_id, dataset, parser_name, parser_version, schema_profile, schema_version)
        if acquisition_mode not in {"API", "FILE", "MIRROR", "MANUAL_IMPORT"}:
            raise ValidationError("Invalid acquisition mode")
        return self._insert("provider_snapshot", dict(snapshot_id=new_id("snap"), provider_id=provider_id,
            dataset=dataset, acquisition_mode=acquisition_mode, upstream_release=upstream_release,
            source_locator=sanitize_locator(source_locator), retrieved_at_utc=utc_now(),
            request_spec_sha256=request_spec_sha256, mirror_commit_sha=mirror_commit_sha,
            parser_name=parser_name, parser_version=parser_version, schema_profile=schema_profile,
            schema_version=schema_version, license_note=license_note, provenance_note=provenance_note,
            metadata_json=json_text(metadata_json), content_manifest_sha256=None, sealed_at_utc=None))

    def get_provider_snapshot(self, snapshot_id: str) -> Row:
        return self._one("provider_snapshot", "snapshot_id", snapshot_id)

    def register_raw_artifact(self, artifact: StoredArtifact, *, media_type: str | None = None,
                              compression: str | None = None) -> Row:
        actual = self.artifacts.verify(artifact.artifact_sha256)
        if actual != artifact:
            raise ArtifactIntegrityError("Artifact descriptor does not match stored bytes")
        with self._transaction():
            existing = self.db.execute("SELECT * FROM raw_artifact WHERE artifact_sha256 = ?",
                                       (artifact.artifact_sha256,)).fetchone()
            if existing is not None:
                row = dict(existing)
                if row["byte_size"] != actual.byte_size or row["relative_path"] != actual.relative_path:
                    raise ArtifactIntegrityError("Artifact registry does not match stored bytes")
                if (media_type is not None and row["media_type"] != media_type) or (
                        compression is not None and row["compression"] != compression):
                    raise ValidationError("Artifact metadata is already immutable")
                return row
            return self._insert("raw_artifact", dict(artifact_sha256=actual.artifact_sha256,
                byte_size=actual.byte_size, media_type=media_type, compression=compression,
                relative_path=actual.relative_path, created_at_utc=utc_now()))

    def attach_artifact_to_snapshot(self, snapshot_id: str, artifact_sha256: str, role: str, ordinal: int, *,
            source_locator: str | None = None, request_metadata_json: str = "{}",
            response_metadata_json: str = "{}") -> Row:
        self._text(snapshot_id, role)
        if type(ordinal) is not int or ordinal < 0:
            raise ValidationError("Invalid artifact ordinal")
        return self._insert("provider_snapshot_artifact", dict(snapshot_id=snapshot_id,
            artifact_sha256=artifact_sha256, role=role, ordinal=ordinal, source_locator=sanitize_locator(source_locator),
            request_metadata_json=json_text(request_metadata_json), response_metadata_json=json_text(response_metadata_json),
            created_at_utc=utc_now()))

    def add_provider_record(self, snapshot_id: str, artifact_sha256: str, record_locator: str, *,
            provider_native_id: str | None = None, ordinal: int | None = None,
            record_content_sha256: str | None = None, metadata_json: str = "{}") -> Row:
        self._text(snapshot_id, record_locator)
        if ordinal is not None and (type(ordinal) is not int or ordinal < 0):
            raise ValidationError("Invalid record ordinal")
        return self._insert("provider_record", dict(provider_record_id=new_id("rec"), snapshot_id=snapshot_id,
            provider_native_id=provider_native_id, artifact_sha256=artifact_sha256, record_locator=record_locator,
            ordinal=ordinal, record_content_sha256=record_content_sha256, observed_at_utc=utc_now(),
            metadata_json=json_text(metadata_json)))

    def list_provider_records(self, snapshot_id: str) -> list[Row]:
        return [dict(r) for r in self.db.execute("SELECT * FROM provider_record WHERE snapshot_id = ? "
                                                "ORDER BY record_locator, provider_record_id", (snapshot_id,))]

    @staticmethod
    def content_manifest(snapshot: Row, links: list[Row], records: list[Row]) -> str:
        """Canonical logical content v1, independent of SQL insertion order and seal timestamp."""
        fields = ("provider_id", "dataset", "acquisition_mode", "upstream_release", "source_locator",
                  "request_spec_sha256", "mirror_commit_sha", "parser_name", "parser_version", "schema_profile",
                  "schema_version", "retrieved_at_utc", "license_note", "provenance_note")
        descriptor = {k: snapshot[k] for k in fields}
        descriptor["source_locator"] = sanitize_locator(descriptor["source_locator"])
        descriptor["metadata"] = json.loads(json_text(snapshot["metadata_json"]))
        attached = []
        for link in links:
            attached.append({**{k: link[k] for k in ("role", "ordinal", "artifact_sha256")},
                "source_locator": sanitize_locator(link["source_locator"]),
                "request_metadata": json.loads(json_text(link["request_metadata_json"])),
                "response_metadata": json.loads(json_text(link["response_metadata_json"]))})
        observed = []
        for record in records:
            observed.append({**{k: record[k] for k in ("provider_record_id", "provider_native_id", "artifact_sha256",
                "record_locator", "ordinal", "record_content_sha256", "observed_at_utc")},
                "metadata": json.loads(json_text(record["metadata_json"]))})
        return canonical_json(dict(manifest_format="foundation.snapshot.v1", snapshot=descriptor,
            artifacts=sorted(attached, key=lambda r: (r["role"], r["ordinal"], r["artifact_sha256"])),
            records=sorted(observed, key=lambda r: (r["record_locator"], r["provider_record_id"]))))

    def _verified_manifest(self, snapshot_id: str) -> str:
        snapshot = self.get_provider_snapshot(snapshot_id)
        links = [dict(r) for r in self.db.execute(
            "SELECT * FROM provider_snapshot_artifact WHERE snapshot_id = ?", (snapshot_id,))]
        records = self.list_provider_records(snapshot_id)
        linked = {link["artifact_sha256"] for link in links}
        if any(record["artifact_sha256"] not in linked for record in records):
            raise ValidationError("Record artifact is not attached to snapshot")
        for digest in linked:
            actual = self.artifacts.verify(digest)
            registered = self._one("raw_artifact", "artifact_sha256", digest)
            if actual.byte_size != registered["byte_size"] or actual.relative_path != registered["relative_path"]:
                raise ArtifactIntegrityError("Artifact registry does not match stored bytes")
        return self.content_manifest(snapshot, links, records)

    def seal_provider_snapshot(self, snapshot_id: str) -> Row:
        with self._transaction():
            snapshot = self.get_provider_snapshot(snapshot_id)
            if snapshot["sealed_at_utc"] is not None:
                raise SnapshotSealedError("Snapshot is already sealed")
            manifest = self._verified_manifest(snapshot_id)
            digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
            self.db._sealing_snapshot = snapshot_id
            try:
                self.db.execute("UPDATE provider_snapshot SET content_manifest_sha256 = ?, sealed_at_utc = ? "
                                "WHERE snapshot_id = ?", (digest, utc_now(), snapshot_id))
            finally:
                self.db._sealing_snapshot = None
        return self.get_provider_snapshot(snapshot_id)

    def verify_provider_snapshot(self, snapshot_id: str) -> bool:
        with self._transaction():
            snapshot = self.get_provider_snapshot(snapshot_id)
            if snapshot["sealed_at_utc"] is None:
                raise ValidationError("Snapshot must be sealed before verification")
            manifest = self._verified_manifest(snapshot_id)
            if hashlib.sha256(manifest.encode("utf-8")).hexdigest() != snapshot["content_manifest_sha256"]:
                raise ArtifactIntegrityError("Snapshot manifest mismatch")
        return True

    def append_audit_event(self, event_type: str, entity_type: str, entity_id: str, actor_type: str, *,
            actor_id: str | None = None, reason_code: str | None = None, note: str | None = None,
            payload_json: str = "{}", supersedes_event_id: str | None = None) -> Row:
        self._text(event_type, entity_type, entity_id)
        if actor_type not in {"SYSTEM", "MANUAL", "IMPORTED"}:
            raise ValidationError("Invalid actor type")
        return self._insert("audit_event", dict(event_id=new_id("evt"), event_type=event_type,
            entity_type=entity_type, entity_id=entity_id, actor_type=actor_type, actor_id=actor_id,
            reason_code=reason_code, note=note, payload_json=json_text(payload_json), created_at_utc=utc_now(),
            supersedes_event_id=supersedes_event_id))

    def list_audit_events(self, *, entity_type: str | None = None, entity_id: str | None = None) -> list[Row]:
        return [dict(r) for r in self.db.execute("SELECT * FROM audit_event WHERE (? IS NULL OR entity_type = ?) "
            "AND (? IS NULL OR entity_id = ?) ORDER BY created_at_utc, event_id",
            (entity_type, entity_type, entity_id, entity_id))]

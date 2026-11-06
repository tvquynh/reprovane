CREATE TABLE review_project (
    project_id TEXT PRIMARY KEY NOT NULL,
    project_key TEXT NOT NULL UNIQUE CHECK(length(project_key) > 0),
    display_name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z')
);

CREATE TABLE project_revision (
    revision_id TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL REFERENCES review_project(project_id),
    revision_number INTEGER NOT NULL CHECK(typeof(revision_number) = 'integer' AND revision_number >= 1),
    parent_revision_id TEXT REFERENCES project_revision(revision_id),
    manifest_format TEXT NOT NULL,
    manifest_text TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256 = foundation_sha256_text(manifest_text)),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z'),
    UNIQUE(project_id, revision_number)
);

CREATE TRIGGER project_revision_parent BEFORE INSERT ON project_revision
WHEN NEW.parent_revision_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM project_revision WHERE revision_id = NEW.parent_revision_id
        AND project_id = NEW.project_id AND revision_number < NEW.revision_number
    ) THEN RAISE(ABORT, 'invalid revision parent') END;
END;

CREATE TABLE provider (
    provider_id TEXT PRIMARY KEY NOT NULL,
    semantic_namespace TEXT NOT NULL UNIQUE CHECK(length(semantic_namespace) > 0),
    display_name TEXT NOT NULL,
    upstream_authority TEXT,
    created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z')
);

CREATE TABLE provider_snapshot (
    snapshot_id TEXT PRIMARY KEY NOT NULL,
    provider_id TEXT NOT NULL REFERENCES provider(provider_id),
    dataset TEXT NOT NULL,
    acquisition_mode TEXT NOT NULL CHECK(acquisition_mode IN ('API','FILE','MIRROR','MANUAL_IMPORT')),
    upstream_release TEXT,
    source_locator TEXT CHECK(source_locator IS foundation_sanitize_locator(source_locator)),
    retrieved_at_utc TEXT NOT NULL CHECK(retrieved_at_utc GLOB '*Z'),
    request_spec_sha256 TEXT CHECK(request_spec_sha256 IS NULL OR
        (length(request_spec_sha256) = 64 AND request_spec_sha256 NOT GLOB '*[^0-9a-f]*')),
    mirror_commit_sha TEXT,
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    schema_profile TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    license_note TEXT,
    provenance_note TEXT,
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
    content_manifest_sha256 TEXT CHECK(content_manifest_sha256 IS NULL OR
        (length(content_manifest_sha256) = 64 AND content_manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    sealed_at_utc TEXT CHECK(sealed_at_utc IS NULL OR sealed_at_utc GLOB '*Z'),
    CHECK((sealed_at_utc IS NULL) = (content_manifest_sha256 IS NULL))
);

CREATE TABLE raw_artifact (
    artifact_sha256 TEXT PRIMARY KEY NOT NULL CHECK(length(artifact_sha256) = 64
        AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_size INTEGER NOT NULL CHECK(typeof(byte_size) = 'integer' AND byte_size >= 0),
    media_type TEXT,
    compression TEXT,
    relative_path TEXT NOT NULL UNIQUE CHECK(relative_path =
        'raw/sha256/' || substr(artifact_sha256, 1, 2) || '/' || artifact_sha256),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z')
);

CREATE TABLE provider_snapshot_artifact (
    snapshot_id TEXT NOT NULL REFERENCES provider_snapshot(snapshot_id),
    artifact_sha256 TEXT NOT NULL REFERENCES raw_artifact(artifact_sha256),
    role TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(typeof(ordinal) = 'integer' AND ordinal >= 0),
    source_locator TEXT CHECK(source_locator IS foundation_sanitize_locator(source_locator)),
    request_metadata_json TEXT NOT NULL CHECK(json_valid(request_metadata_json)),
    response_metadata_json TEXT NOT NULL CHECK(json_valid(response_metadata_json)),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z'),
    PRIMARY KEY(snapshot_id, role, ordinal)
);

CREATE TABLE provider_record (
    provider_record_id TEXT PRIMARY KEY NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES provider_snapshot(snapshot_id),
    provider_native_id TEXT,
    artifact_sha256 TEXT NOT NULL REFERENCES raw_artifact(artifact_sha256),
    record_locator TEXT NOT NULL,
    ordinal INTEGER CHECK(ordinal IS NULL OR (typeof(ordinal) = 'integer' AND ordinal >= 0)),
    record_content_sha256 TEXT CHECK(record_content_sha256 IS NULL OR
        (length(record_content_sha256) = 64 AND record_content_sha256 NOT GLOB '*[^0-9a-f]*')),
    observed_at_utc TEXT NOT NULL CHECK(observed_at_utc GLOB '*Z'),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
    UNIQUE(snapshot_id, record_locator)
);

CREATE TABLE audit_event (
    event_id TEXT PRIMARY KEY NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('SYSTEM','MANUAL','IMPORTED')),
    actor_id TEXT,
    reason_code TEXT,
    note TEXT,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z'),
    supersedes_event_id TEXT REFERENCES audit_event(event_id)
);

CREATE INDEX provider_record_snapshot ON provider_record(snapshot_id);
CREATE INDEX audit_event_entity ON audit_event(entity_type, entity_id);

CREATE TRIGGER snapshot_insert_open BEFORE INSERT ON provider_snapshot
WHEN NEW.sealed_at_utc IS NOT NULL OR NEW.content_manifest_sha256 IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'snapshot must start open'); END;

CREATE TRIGGER snapshot_update_guard BEFORE UPDATE ON provider_snapshot
BEGIN
    SELECT CASE WHEN OLD.sealed_at_utc IS NOT NULL
        THEN RAISE(ABORT, 'snapshot sealed') END;
    SELECT CASE WHEN NEW.sealed_at_utc IS NULL OR NEW.content_manifest_sha256 IS NULL
        OR foundation_seal_authorized(OLD.snapshot_id) != 1
        OR NEW.snapshot_id IS NOT OLD.snapshot_id
        OR NEW.provider_id IS NOT OLD.provider_id
        OR NEW.dataset IS NOT OLD.dataset
        OR NEW.acquisition_mode IS NOT OLD.acquisition_mode
        OR NEW.upstream_release IS NOT OLD.upstream_release
        OR NEW.source_locator IS NOT OLD.source_locator
        OR NEW.retrieved_at_utc IS NOT OLD.retrieved_at_utc
        OR NEW.request_spec_sha256 IS NOT OLD.request_spec_sha256
        OR NEW.mirror_commit_sha IS NOT OLD.mirror_commit_sha
        OR NEW.parser_name IS NOT OLD.parser_name
        OR NEW.parser_version IS NOT OLD.parser_version
        OR NEW.schema_profile IS NOT OLD.schema_profile
        OR NEW.schema_version IS NOT OLD.schema_version
        OR NEW.license_note IS NOT OLD.license_note
        OR NEW.provenance_note IS NOT OLD.provenance_note
        OR NEW.metadata_json IS NOT OLD.metadata_json
        THEN RAISE(ABORT, 'unauthorized seal transition') END;
END;

CREATE TRIGGER snapshot_delete_guard BEFORE DELETE ON provider_snapshot
BEGIN
    SELECT CASE WHEN OLD.sealed_at_utc IS NOT NULL
        THEN RAISE(ABORT, 'snapshot sealed') ELSE RAISE(ABORT, 'immutable snapshot') END;
END;

CREATE TRIGGER snapshot_artifact_open BEFORE INSERT ON provider_snapshot_artifact
BEGIN
    SELECT CASE WHEN (SELECT sealed_at_utc FROM provider_snapshot WHERE snapshot_id = NEW.snapshot_id) IS NOT NULL
        THEN RAISE(ABORT, 'snapshot sealed') END;
END;

CREATE TRIGGER provider_record_open BEFORE INSERT ON provider_record
BEGIN
    SELECT CASE WHEN (SELECT sealed_at_utc FROM provider_snapshot WHERE snapshot_id = NEW.snapshot_id) IS NOT NULL
        THEN RAISE(ABORT, 'snapshot sealed') END;
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM provider_snapshot_artifact
        WHERE snapshot_id = NEW.snapshot_id AND artifact_sha256 = NEW.artifact_sha256)
        THEN RAISE(ABORT, 'record artifact not attached to snapshot') END;
END;

CREATE TRIGGER review_project_no_update BEFORE UPDATE ON review_project
BEGIN SELECT RAISE(ABORT, 'immutable review_project'); END;
CREATE TRIGGER review_project_no_delete BEFORE DELETE ON review_project
BEGIN SELECT RAISE(ABORT, 'immutable review_project'); END;
CREATE TRIGGER project_revision_no_update BEFORE UPDATE ON project_revision
BEGIN SELECT RAISE(ABORT, 'immutable project_revision'); END;
CREATE TRIGGER project_revision_no_delete BEFORE DELETE ON project_revision
BEGIN SELECT RAISE(ABORT, 'immutable project_revision'); END;
CREATE TRIGGER provider_no_update BEFORE UPDATE ON provider
BEGIN SELECT RAISE(ABORT, 'immutable provider'); END;
CREATE TRIGGER provider_no_delete BEFORE DELETE ON provider
BEGIN SELECT RAISE(ABORT, 'immutable provider'); END;
CREATE TRIGGER raw_artifact_no_update BEFORE UPDATE ON raw_artifact
BEGIN SELECT RAISE(ABORT, 'immutable raw_artifact'); END;
CREATE TRIGGER raw_artifact_no_delete BEFORE DELETE ON raw_artifact
BEGIN SELECT RAISE(ABORT, 'immutable raw_artifact'); END;
CREATE TRIGGER snapshot_artifact_no_update BEFORE UPDATE ON provider_snapshot_artifact
BEGIN SELECT RAISE(ABORT, 'immutable provider_snapshot_artifact'); END;
CREATE TRIGGER snapshot_artifact_no_delete BEFORE DELETE ON provider_snapshot_artifact
BEGIN SELECT RAISE(ABORT, 'immutable provider_snapshot_artifact'); END;
CREATE TRIGGER provider_record_no_update BEFORE UPDATE ON provider_record
BEGIN SELECT RAISE(ABORT, 'immutable provider_record'); END;
CREATE TRIGGER provider_record_no_delete BEFORE DELETE ON provider_record
BEGIN SELECT RAISE(ABORT, 'immutable provider_record'); END;
CREATE TRIGGER audit_event_no_update BEFORE UPDATE ON audit_event
BEGIN SELECT RAISE(ABORT, 'immutable audit_event'); END;
CREATE TRIGGER audit_event_no_delete BEFORE DELETE ON audit_event
BEGIN SELECT RAISE(ABORT, 'immutable audit_event'); END;

-- BEFORE INSERT guards also defeat INSERT OR REPLACE on an external connection
-- where recursive_triggers is OFF. DELETE guards alone cannot guarantee this.
CREATE TRIGGER review_project_duplicate_guard BEFORE INSERT ON review_project
WHEN EXISTS (SELECT 1 FROM review_project WHERE project_id=NEW.project_id OR project_key=NEW.project_key)
BEGIN SELECT RAISE(ABORT, 'duplicate identity'); END;
CREATE TRIGGER project_revision_duplicate_guard BEFORE INSERT ON project_revision
WHEN EXISTS (SELECT 1 FROM project_revision WHERE revision_id=NEW.revision_id
    OR (project_id=NEW.project_id AND revision_number=NEW.revision_number))
BEGIN SELECT RAISE(ABORT, 'duplicate identity'); END;
CREATE TRIGGER provider_duplicate_guard BEFORE INSERT ON provider
WHEN EXISTS (SELECT 1 FROM provider WHERE provider_id=NEW.provider_id OR semantic_namespace=NEW.semantic_namespace)
BEGIN SELECT RAISE(ABORT, 'duplicate identity'); END;
CREATE TRIGGER snapshot_duplicate_guard BEFORE INSERT ON provider_snapshot
WHEN EXISTS (SELECT 1 FROM provider_snapshot WHERE snapshot_id=NEW.snapshot_id)
BEGIN SELECT RAISE(ABORT, 'duplicate identity'); END;
CREATE TRIGGER raw_artifact_duplicate_guard BEFORE INSERT ON raw_artifact
WHEN EXISTS (SELECT 1 FROM raw_artifact WHERE artifact_sha256=NEW.artifact_sha256 OR relative_path=NEW.relative_path)
BEGIN SELECT RAISE(ABORT, 'duplicate identity'); END;
CREATE TRIGGER snapshot_artifact_duplicate_guard BEFORE INSERT ON provider_snapshot_artifact
WHEN EXISTS (SELECT 1 FROM provider_snapshot_artifact
    WHERE snapshot_id=NEW.snapshot_id AND role=NEW.role AND ordinal=NEW.ordinal)
BEGIN SELECT RAISE(ABORT, 'duplicate identity'); END;
CREATE TRIGGER provider_record_duplicate_guard BEFORE INSERT ON provider_record
WHEN EXISTS (SELECT 1 FROM provider_record WHERE provider_record_id=NEW.provider_record_id
    OR (snapshot_id=NEW.snapshot_id AND record_locator=NEW.record_locator))
BEGIN SELECT RAISE(ABORT, 'duplicate identity'); END;
CREATE TRIGGER audit_event_duplicate_guard BEFORE INSERT ON audit_event
WHEN EXISTS (SELECT 1 FROM audit_event WHERE event_id=NEW.event_id)
BEGIN SELECT RAISE(ABORT, 'duplicate identity'); END;

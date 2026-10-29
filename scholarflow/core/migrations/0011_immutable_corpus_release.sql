CREATE TABLE corpus_release (
 release_id TEXT PRIMARY KEY NOT NULL, project_revision_id TEXT NOT NULL REFERENCES project_revision(revision_id),
 screening_run_id TEXT NOT NULL REFERENCES screening_run(screening_run_id), screening_manifest_sha256 TEXT NOT NULL,
 release_plan_json TEXT NOT NULL CHECK(json_valid(release_plan_json)), release_plan_sha256 TEXT NOT NULL CHECK(release_plan_sha256=foundation_sha256_text(release_plan_json)),
 source_json TEXT NOT NULL CHECK(json_valid(source_json)), source_sha256 TEXT NOT NULL CHECK(source_sha256=foundation_sha256_text(source_json)),
 state TEXT NOT NULL CHECK(state IN ('OPEN','MATERIALIZED','SEALED')), item_set_sha256 TEXT,
 release_manifest_json TEXT CHECK(release_manifest_json IS NULL OR json_valid(release_manifest_json)), release_manifest_sha256 TEXT,
 created_at_utc TEXT NOT NULL, sealed_at_utc TEXT,
 UNIQUE(screening_run_id,release_plan_sha256),
 CHECK((state='OPEN')=(item_set_sha256 IS NULL)), CHECK((state='OPEN')=(release_manifest_json IS NULL)),
 CHECK((state='OPEN')=(release_manifest_sha256 IS NULL)), CHECK(release_manifest_sha256 IS NULL OR release_manifest_sha256=foundation_sha256_text(release_manifest_json)),
 CHECK((state='SEALED')=(sealed_at_utc IS NOT NULL))
);
CREATE TABLE corpus_release_item (
 corpus_item_id TEXT PRIMARY KEY NOT NULL, release_id TEXT NOT NULL REFERENCES corpus_release(release_id), publication_entity_id TEXT NOT NULL,
 screening_candidate_id TEXT NOT NULL, screening_resolution_id TEXT NOT NULL,
 resolution_run_id TEXT NOT NULL, ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 projection_refs_json TEXT NOT NULL CHECK(json_valid(projection_refs_json)),
 identity_json TEXT NOT NULL CHECK(json_valid(identity_json)), identity_sha256 TEXT NOT NULL CHECK(identity_sha256=foundation_sha256_text(identity_json)),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)), metadata_sha256 TEXT NOT NULL CHECK(metadata_sha256=foundation_sha256_text(metadata_json)),
 screening_provenance_json TEXT NOT NULL CHECK(json_valid(screening_provenance_json)), screening_provenance_sha256 TEXT NOT NULL CHECK(screening_provenance_sha256=foundation_sha256_text(screening_provenance_json)),
 discovery_provenance_json TEXT NOT NULL CHECK(json_valid(discovery_provenance_json)), discovery_provenance_sha256 TEXT NOT NULL CHECK(discovery_provenance_sha256=foundation_sha256_text(discovery_provenance_json)),
 oa_locations_json TEXT NOT NULL CHECK(json_valid(oa_locations_json)), oa_locations_sha256 TEXT NOT NULL CHECK(oa_locations_sha256=foundation_sha256_text(oa_locations_json)), item_sha256 TEXT NOT NULL,
 UNIQUE(release_id,publication_entity_id), UNIQUE(release_id,ordinal),
 FOREIGN KEY(resolution_run_id,publication_entity_id) REFERENCES publication_entity(resolution_run_id,entity_id),
 FOREIGN KEY(screening_resolution_id,screening_candidate_id) REFERENCES screening_outcome(resolution_id,candidate_id)
);
CREATE TABLE corpus_release_export (
 release_id TEXT PRIMARY KEY NOT NULL REFERENCES corpus_release(release_id), export_schema TEXT NOT NULL CHECK(export_schema='corpus-portable/1'),
 file_inventory_json TEXT NOT NULL CHECK(json_valid(file_inventory_json)), manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
 export_manifest_sha256 TEXT NOT NULL CHECK(export_manifest_sha256=foundation_sha256_text(manifest_json))
);
CREATE VIEW corpus_release_membership AS SELECT i.* FROM corpus_release_item i JOIN corpus_release r ON r.release_id=i.release_id WHERE r.state='SEALED';
CREATE TRIGGER corpus_release_insert BEFORE INSERT ON corpus_release BEGIN
 SELECT CASE WHEN corpus_release_authorized(NEW.release_id,'CREATE')!=1 OR EXISTS(SELECT 1 FROM corpus_release WHERE release_id=NEW.release_id OR (screening_run_id=NEW.screening_run_id AND release_plan_sha256=NEW.release_plan_sha256)) THEN RAISE(ABORT,'immutable corpus identity') END;
 SELECT CASE WHEN NEW.state!='OPEN' OR NOT EXISTS(SELECT 1 FROM screening_run s WHERE s.screening_run_id=NEW.screening_run_id AND s.project_revision_id=NEW.project_revision_id AND s.state='SEALED' AND s.finalization_sha256=NEW.screening_manifest_sha256) THEN RAISE(ABORT,'corpus requires exact sealed screening') END;
END;
CREATE TRIGGER corpus_release_delete BEFORE DELETE ON corpus_release BEGIN SELECT RAISE(ABORT,'immutable corpus evidence'); END;
CREATE TRIGGER corpus_release_item_insert BEFORE INSERT ON corpus_release_item BEGIN
 SELECT CASE WHEN corpus_release_authorized(NEW.release_id,'MATERIALIZE')!=1 OR EXISTS(SELECT 1 FROM corpus_release_item WHERE corpus_item_id=NEW.corpus_item_id OR (release_id=NEW.release_id AND (publication_entity_id=NEW.publication_entity_id OR ordinal=NEW.ordinal))) THEN RAISE(ABORT,'immutable corpus identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM corpus_release WHERE release_id=NEW.release_id AND state='OPEN') THEN RAISE(ABORT,'corpus membership locked') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM corpus_release r JOIN screening_run s ON s.screening_run_id=r.screening_run_id JOIN screening_outcome o ON o.resolution_id=s.current_resolution_id JOIN screening_candidate c ON c.candidate_id=o.candidate_id WHERE r.release_id=NEW.release_id AND s.state='SEALED' AND o.outcome='SCREEN_INCLUDE' AND o.candidate_id=NEW.screening_candidate_id AND o.resolution_id=NEW.screening_resolution_id AND o.publication_entity_id=NEW.publication_entity_id AND c.discovery_resolution_run_id=NEW.resolution_run_id) THEN RAISE(ABORT,'item not a final screening include') END;
END;
CREATE TRIGGER corpus_release_item_delete BEFORE DELETE ON corpus_release_item BEGIN SELECT RAISE(ABORT,'immutable corpus evidence'); END;
CREATE TRIGGER corpus_release_item_update BEFORE UPDATE ON corpus_release_item BEGIN SELECT RAISE(ABORT,'immutable corpus evidence'); END;
CREATE TRIGGER corpus_release_export_insert BEFORE INSERT ON corpus_release_export BEGIN
 SELECT CASE WHEN corpus_release_authorized(NEW.release_id,'MATERIALIZE')!=1 OR EXISTS(SELECT 1 FROM corpus_release_export WHERE release_id=NEW.release_id) THEN RAISE(ABORT,'immutable corpus identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM corpus_release WHERE release_id=NEW.release_id AND state='OPEN') THEN RAISE(ABORT,'corpus membership locked') END;
END;
CREATE TRIGGER corpus_release_export_delete BEFORE DELETE ON corpus_release_export BEGIN SELECT RAISE(ABORT,'immutable corpus evidence'); END;
CREATE TRIGGER corpus_release_export_update BEFORE UPDATE ON corpus_release_export BEGIN SELECT RAISE(ABORT,'immutable corpus evidence'); END;
CREATE TRIGGER corpus_release_update BEFORE UPDATE ON corpus_release BEGIN SELECT CASE WHEN OLD.state='SEALED' OR NEW.release_id IS NOT OLD.release_id OR NEW.project_revision_id IS NOT OLD.project_revision_id OR NEW.screening_run_id IS NOT OLD.screening_run_id OR NEW.screening_manifest_sha256 IS NOT OLD.screening_manifest_sha256 OR NEW.release_plan_json IS NOT OLD.release_plan_json OR NEW.release_plan_sha256 IS NOT OLD.release_plan_sha256 OR NEW.source_json IS NOT OLD.source_json OR NEW.source_sha256 IS NOT OLD.source_sha256 OR NEW.created_at_utc IS NOT OLD.created_at_utc OR NOT (OLD.state='OPEN' AND NEW.state='MATERIALIZED' AND corpus_release_authorized(OLD.release_id,'MATERIALIZE')=1 OR OLD.state='MATERIALIZED' AND NEW.state='SEALED' AND corpus_release_authorized(OLD.release_id,'SEAL')=1 AND NEW.item_set_sha256 IS OLD.item_set_sha256 AND NEW.release_manifest_json IS OLD.release_manifest_json AND NEW.release_manifest_sha256 IS OLD.release_manifest_sha256) THEN RAISE(ABORT,'immutable corpus transition') END; END;

CREATE TABLE openalex_acquisition (
 acquisition_id TEXT PRIMARY KEY NOT NULL,
 project_revision_id TEXT NOT NULL REFERENCES project_revision(revision_id),
 request_spec_json TEXT NOT NULL CHECK(json_valid(request_spec_json)),
 request_spec_sha256 TEXT NOT NULL CHECK(length(request_spec_sha256)=64 AND request_spec_sha256=foundation_sha256_text(request_spec_json)),
 state TEXT NOT NULL CHECK(state IN ('OPEN','INTERRUPTED','COMPLETE')),
 page_count INTEGER NOT NULL CHECK(page_count>=0),
 work_count INTEGER NOT NULL CHECK(work_count>=0),
 next_cursor TEXT,
 termination TEXT CHECK(termination IS NULL OR termination IN ('NATURAL','RECORD_LIMIT','PAGE_LIMIT')),
 error_code TEXT,
 snapshot_id TEXT REFERENCES provider_snapshot(snapshot_id),
 source_snapshot_id TEXT REFERENCES provider_snapshot(snapshot_id),
 created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z'),
 updated_at_utc TEXT NOT NULL CHECK(updated_at_utc GLOB '*Z'),
 UNIQUE(project_revision_id,request_spec_sha256),
 CHECK((state='COMPLETE')=(snapshot_id IS NOT NULL)),
 CHECK(state!='COMPLETE' OR termination IS NOT NULL)
);
CREATE TABLE openalex_acquisition_page (
 acquisition_id TEXT NOT NULL REFERENCES openalex_acquisition(acquisition_id),
 page_index INTEGER NOT NULL CHECK(page_index>=1),
 cursor_in TEXT NOT NULL,
 cursor_out TEXT,
 artifact_sha256 TEXT NOT NULL REFERENCES raw_artifact(artifact_sha256),
 byte_size INTEGER NOT NULL CHECK(byte_size>=0),
 request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 response_metadata_json TEXT NOT NULL CHECK(json_valid(response_metadata_json)),
 raw_record_count INTEGER NOT NULL CHECK(raw_record_count>=0),
 retrieved_at_utc TEXT NOT NULL CHECK(retrieved_at_utc GLOB '*Z'),
 PRIMARY KEY(acquisition_id,page_index), UNIQUE(acquisition_id,cursor_in)
);
CREATE UNIQUE INDEX openalex_record_native ON provider_record(snapshot_id,provider_native_id)
WHERE json_extract(metadata_json,'$.openalex_acquisition') IS NOT NULL;
CREATE TRIGGER oa_run_insert BEFORE INSERT ON openalex_acquisition
BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM openalex_acquisition WHERE acquisition_id=NEW.acquisition_id OR (project_revision_id=NEW.project_revision_id AND request_spec_sha256=NEW.request_spec_sha256)) THEN RAISE(ABORT,'immutable acquisition identity') END;
 SELECT CASE WHEN NEW.state!='OPEN' OR NEW.page_count!=0 OR NEW.work_count!=0 OR NEW.next_cursor!='*' OR NEW.termination IS NOT NULL OR NEW.snapshot_id IS NOT NULL OR NEW.source_snapshot_id IS NOT NULL THEN RAISE(ABORT,'acquisition must start open') END;
END;
CREATE TRIGGER oa_run_no_delete BEFORE DELETE ON openalex_acquisition BEGIN SELECT RAISE(ABORT,'immutable acquisition'); END;
CREATE TRIGGER oa_run_update BEFORE UPDATE ON openalex_acquisition
BEGIN
 SELECT CASE WHEN OLD.state='COMPLETE' OR openalex_acquisition_authorized(OLD.acquisition_id)!=1 OR NEW.acquisition_id IS NOT OLD.acquisition_id OR NEW.project_revision_id IS NOT OLD.project_revision_id OR NEW.request_spec_json IS NOT OLD.request_spec_json OR NEW.request_spec_sha256 IS NOT OLD.request_spec_sha256 OR NEW.created_at_utc IS NOT OLD.created_at_utc THEN RAISE(ABORT,'immutable acquisition identity or unauthorized transition') END;
 SELECT CASE WHEN NEW.state='COMPLETE' AND NOT EXISTS(SELECT 1 FROM provider_snapshot s WHERE s.snapshot_id=NEW.snapshot_id AND s.sealed_at_utc IS NOT NULL AND s.request_spec_sha256=NEW.request_spec_sha256) THEN RAISE(ABORT,'acquisition completion requires sealed matching snapshot') END;
END;
CREATE TRIGGER oa_page_insert BEFORE INSERT ON openalex_acquisition_page
BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM openalex_acquisition_page WHERE acquisition_id=NEW.acquisition_id AND (page_index=NEW.page_index OR cursor_in=NEW.cursor_in)) THEN RAISE(ABORT,'immutable acquisition page') END;
 SELECT CASE WHEN openalex_acquisition_authorized(NEW.acquisition_id)!=1 OR NOT EXISTS(SELECT 1 FROM openalex_acquisition r WHERE r.acquisition_id=NEW.acquisition_id AND r.state!='COMPLETE' AND r.termination IS NULL AND NEW.page_index=r.page_count+1 AND NEW.cursor_in=r.next_cursor) THEN RAISE(ABORT,'acquisition page sequence locked') END;
END;
CREATE TRIGGER oa_page_update BEFORE UPDATE ON openalex_acquisition_page BEGIN SELECT RAISE(ABORT,'immutable acquisition page'); END;
CREATE TRIGGER oa_page_delete BEFORE DELETE ON openalex_acquisition_page BEGIN SELECT RAISE(ABORT,'immutable acquisition page'); END;

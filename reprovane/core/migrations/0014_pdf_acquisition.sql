-- Unified PDF acquisition: discovery-oriented runs, manual ingestion, review supplements and host state.
-- Review-oriented Stage11B tables (fulltext_*) and migrations 0001-0013 are unchanged.
CREATE TABLE pdf_run (
 run_id TEXT PRIMARY KEY NOT NULL,
 profile TEXT NOT NULL CHECK(profile='DISCOVERY_DOWNLOAD'),
 label TEXT NOT NULL,
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 policy_sha256 TEXT NOT NULL CHECK(policy_sha256=foundation_sha256_text(policy_json)),
 state TEXT NOT NULL CHECK(state IN ('OPEN','DISCOVERING','ACQUIRING','CLOSED')),
 identity_json TEXT CHECK(identity_json IS NULL OR json_valid(identity_json)),
 identity_sha256 TEXT CHECK(identity_sha256 IS NULL OR identity_sha256=foundation_sha256_text(identity_json)),
 manifest_json TEXT CHECK(manifest_json IS NULL OR json_valid(manifest_json)),
 manifest_sha256 TEXT CHECK(manifest_sha256 IS NULL OR manifest_sha256=foundation_sha256_text(manifest_json)),
 created_at_utc TEXT NOT NULL,
 closed_at_utc TEXT,
 CHECK((identity_json IS NULL)=(identity_sha256 IS NULL)),
 CHECK((manifest_json IS NULL)=(manifest_sha256 IS NULL)),
 CHECK((state='OPEN')=(identity_json IS NULL)),
 CHECK((state='CLOSED')=(manifest_json IS NOT NULL)),
 CHECK((state='CLOSED')=(closed_at_utc IS NOT NULL))
);
CREATE TABLE pdf_input_batch (
 batch_id TEXT PRIMARY KEY NOT NULL,
 run_id TEXT NOT NULL REFERENCES pdf_run(run_id),
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 source_kind TEXT NOT NULL CHECK(source_kind IN ('FILE','IDENTIFIERS','OPENALEX_ACQUISITION','DISCOVERY_RUN','CORPUS_RELEASE')),
 source_format TEXT NOT NULL,
 source_label TEXT NOT NULL,
 source_sha256 TEXT NOT NULL,
 raw_artifact_sha256 TEXT REFERENCES raw_artifact(artifact_sha256),
 batch_json TEXT NOT NULL CHECK(json_valid(batch_json)),
 batch_sha256 TEXT NOT NULL CHECK(batch_sha256=foundation_sha256_text(batch_json)),
 imported_at_utc TEXT NOT NULL,
 UNIQUE(run_id,ordinal), UNIQUE(run_id,source_kind,source_sha256)
);
CREATE TABLE pdf_input_record (
 run_id TEXT NOT NULL REFERENCES pdf_run(run_id),
 record_id TEXT NOT NULL,
 batch_id TEXT NOT NULL REFERENCES pdf_input_batch(batch_id),
 row_ordinal INTEGER NOT NULL CHECK(row_ordinal>=0),
 status TEXT NOT NULL CHECK(status IN ('ACCEPTED','REJECTED')),
 reason TEXT,
 record_json TEXT NOT NULL CHECK(json_valid(record_json)),
 record_sha256 TEXT NOT NULL CHECK(record_sha256=foundation_sha256_text(record_json)),
 PRIMARY KEY(run_id,record_id), UNIQUE(batch_id,row_ordinal),
 CHECK((status='REJECTED')=(reason IS NOT NULL))
);
CREATE TABLE pdf_work (
 run_id TEXT NOT NULL REFERENCES pdf_run(run_id),
 work_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 work_key TEXT NOT NULL,
 identity_json TEXT NOT NULL CHECK(json_valid(identity_json)),
 identity_sha256 TEXT NOT NULL CHECK(identity_sha256=foundation_sha256_text(identity_json)),
 PRIMARY KEY(run_id,work_id), UNIQUE(run_id,ordinal), UNIQUE(run_id,work_key)
);
CREATE TABLE pdf_evidence (
 run_id TEXT NOT NULL REFERENCES pdf_run(run_id),
 evidence_id TEXT NOT NULL,
 provider TEXT NOT NULL CHECK(provider IN ('OPENALEX','UNPAYWALL')),
 linkage TEXT NOT NULL,
 response_sha256 TEXT,
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 metadata_sha256 TEXT NOT NULL CHECK(metadata_sha256=foundation_sha256_text(metadata_json)),
 retrieved_at_utc TEXT NOT NULL,
 PRIMARY KEY(run_id,evidence_id), UNIQUE(run_id,provider,linkage)
);
CREATE TABLE pdf_plan (
 run_id TEXT NOT NULL,
 work_id TEXT NOT NULL,
 plan_json TEXT NOT NULL CHECK(json_valid(plan_json)),
 plan_sha256 TEXT NOT NULL CHECK(plan_sha256=foundation_sha256_text(plan_json)),
 PRIMARY KEY(run_id,work_id), FOREIGN KEY(run_id,work_id) REFERENCES pdf_work(run_id,work_id)
);
CREATE TABLE pdf_candidate (
 run_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL,
 work_id TEXT NOT NULL,
 rank INTEGER NOT NULL CHECK(rank>=0),
 eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
 candidate_json TEXT NOT NULL CHECK(json_valid(candidate_json)),
 candidate_sha256 TEXT NOT NULL CHECK(candidate_sha256=foundation_sha256_text(candidate_json)),
 PRIMARY KEY(run_id,candidate_id), UNIQUE(run_id,work_id,rank),
 FOREIGN KEY(run_id,work_id) REFERENCES pdf_plan(run_id,work_id)
);
CREATE TABLE pdf_request (
 run_id TEXT NOT NULL REFERENCES pdf_run(run_id),
 request_id TEXT NOT NULL,
 purpose TEXT NOT NULL CHECK(purpose IN ('PDF','METADATA','ROBOTS')),
 reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes>=0),
 request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 request_sha256 TEXT NOT NULL CHECK(request_sha256=foundation_sha256_text(request_json)),
 created_at_utc TEXT NOT NULL,
 PRIMARY KEY(run_id,request_id)
);
CREATE TABLE pdf_request_result (
 run_id TEXT NOT NULL,
 request_id TEXT NOT NULL,
 bytes INTEGER NOT NULL CHECK(bytes>=0),
 result_json TEXT NOT NULL CHECK(json_valid(result_json)),
 result_sha256 TEXT NOT NULL CHECK(result_sha256=foundation_sha256_text(result_json)),
 finished_at_utc TEXT NOT NULL,
 PRIMARY KEY(run_id,request_id), FOREIGN KEY(run_id,request_id) REFERENCES pdf_request(run_id,request_id)
);
CREATE TABLE pdf_artifact (
 artifact_sha256 TEXT PRIMARY KEY NOT NULL REFERENCES raw_artifact(artifact_sha256),
 byte_size INTEGER NOT NULL CHECK(byte_size>0),
 validation_json TEXT NOT NULL CHECK(json_valid(validation_json)),
 validation_sha256 TEXT NOT NULL CHECK(validation_sha256=foundation_sha256_text(validation_json)),
 registered_at_utc TEXT NOT NULL
);
CREATE TABLE pdf_attempt (
 run_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 started_at_utc TEXT NOT NULL,
 PRIMARY KEY(run_id,attempt_id), UNIQUE(run_id,candidate_id,ordinal),
 FOREIGN KEY(run_id,candidate_id) REFERENCES pdf_candidate(run_id,candidate_id)
);
CREATE TABLE pdf_attempt_result (
 run_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('ACQUIRED','FAILED')),
 failure_class TEXT,
 artifact_sha256 TEXT REFERENCES pdf_artifact(artifact_sha256),
 result_json TEXT NOT NULL CHECK(json_valid(result_json)),
 result_sha256 TEXT NOT NULL CHECK(result_sha256=foundation_sha256_text(result_json)),
 finished_at_utc TEXT NOT NULL,
 PRIMARY KEY(run_id,attempt_id), FOREIGN KEY(run_id,attempt_id) REFERENCES pdf_attempt(run_id,attempt_id),
 CHECK((outcome='ACQUIRED')=(artifact_sha256 IS NOT NULL)),
 CHECK((outcome='FAILED')=(failure_class IS NOT NULL))
);
CREATE TABLE pdf_import_session (
 session_id TEXT PRIMARY KEY NOT NULL,
 context_kind TEXT NOT NULL CHECK(context_kind IN ('DISCOVERY_RUN','REVIEW_FULLTEXT_RUN')),
 context_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('MANUAL','LEGACY_V4_FULLTEXT')),
 source_label TEXT NOT NULL,
 session_json TEXT NOT NULL CHECK(json_valid(session_json)),
 session_sha256 TEXT NOT NULL CHECK(session_sha256=foundation_sha256_text(session_json)),
 created_at_utc TEXT NOT NULL
);
CREATE TABLE pdf_manual_import (
 import_id TEXT PRIMARY KEY NOT NULL,
 session_id TEXT NOT NULL REFERENCES pdf_import_session(session_id),
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 source_name TEXT NOT NULL,
 source_sha256 TEXT,
 byte_size INTEGER,
 outcome TEXT NOT NULL CHECK(outcome IN ('LINKED','ALREADY_LINKED','RECONCILIATION_REQUIRED','REJECTED')),
 reason TEXT,
 artifact_sha256 TEXT REFERENCES pdf_artifact(artifact_sha256),
 detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
 detail_sha256 TEXT NOT NULL CHECK(detail_sha256=foundation_sha256_text(detail_json)),
 UNIQUE(session_id,ordinal),
 CHECK((outcome='REJECTED')=(artifact_sha256 IS NULL))
);
CREATE TABLE pdf_work_artifact (
 link_id TEXT PRIMARY KEY NOT NULL,
 run_id TEXT NOT NULL,
 work_id TEXT NOT NULL,
 artifact_sha256 TEXT NOT NULL REFERENCES pdf_artifact(artifact_sha256),
 source TEXT NOT NULL CHECK(source IN ('AUTO','MANUAL','LEGACY_V4')),
 attempt_id TEXT,
 import_id TEXT REFERENCES pdf_manual_import(import_id),
 basis TEXT NOT NULL,
 version_claim TEXT NOT NULL CHECK(version_claim IN ('publishedVersion','acceptedVersion','submittedVersion','UNKNOWN')),
 verification TEXT NOT NULL,
 created_at_utc TEXT NOT NULL,
 FOREIGN KEY(run_id,work_id) REFERENCES pdf_work(run_id,work_id),
 FOREIGN KEY(run_id,attempt_id) REFERENCES pdf_attempt(run_id,attempt_id),
 UNIQUE(run_id,work_id,artifact_sha256,source),
 CHECK((source='AUTO')=(attempt_id IS NOT NULL)),
 CHECK((source='AUTO')=(import_id IS NULL))
);
CREATE TABLE pdf_review_supplement (
 link_id TEXT PRIMARY KEY NOT NULL,
 fulltext_run_id TEXT NOT NULL,
 corpus_item_id TEXT NOT NULL,
 artifact_sha256 TEXT NOT NULL REFERENCES pdf_artifact(artifact_sha256),
 import_id TEXT NOT NULL REFERENCES pdf_manual_import(import_id),
 basis TEXT NOT NULL,
 verification TEXT NOT NULL CHECK(verification='SUPPLEMENTARY_UNVERIFIED_NOT_PRIMARY'),
 created_at_utc TEXT NOT NULL,
 FOREIGN KEY(fulltext_run_id,corpus_item_id) REFERENCES fulltext_input(run_id,corpus_item_id),
 UNIQUE(fulltext_run_id,corpus_item_id,artifact_sha256)
);
CREATE TABLE pdf_reconciliation (
 item_id TEXT PRIMARY KEY NOT NULL,
 context_kind TEXT NOT NULL CHECK(context_kind IN ('DISCOVERY_RUN','REVIEW_FULLTEXT_RUN')),
 context_id TEXT NOT NULL,
 import_id TEXT NOT NULL UNIQUE REFERENCES pdf_manual_import(import_id),
 kind TEXT NOT NULL CHECK(kind IN ('UNMATCHED','AMBIGUOUS','IDENTIFIER_NOT_IN_CONTEXT','IDENTIFIER_CONFLICT','ARTIFACT_LINKED_ELSEWHERE',
  'MAPPING_CONFLICT','KEY_NEEDS_CONFIRMATION')),
 candidates_json TEXT NOT NULL CHECK(json_valid(candidates_json)),
 created_at_utc TEXT NOT NULL
);
CREATE TABLE pdf_reconciliation_decision (
 item_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_reconciliation(item_id),
 decision TEXT NOT NULL CHECK(decision IN ('LINK','REJECT')),
 target_id TEXT,
 decided_by TEXT NOT NULL,
 decided_at_utc TEXT NOT NULL,
 CHECK((decision='LINK')=(target_id IS NOT NULL))
);
CREATE TABLE pdf_host_event (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 host TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('BLOCK','UNBLOCK','COOLDOWN')),
 reason TEXT NOT NULL,
 http_status INTEGER,
 url TEXT,
 until_utc TEXT,
 run_id TEXT,
 origin TEXT NOT NULL CHECK(origin IN ('ENGINE','OPERATOR','LEGACY_V4_IMPORT')),
 created_at_utc TEXT NOT NULL,
 CHECK((kind='COOLDOWN')=(until_utc IS NOT NULL))
);
-- Politeness applied to Stage11B review acquisition by `reprovane pdf review acquire`. robots.txt requests are kept
-- here, outside the hash-verified Stage11B request ledger, which accepts only PDF and METADATA requests. They are
-- reserved before any network I/O and count against the review run's frozen request and byte budget.
CREATE TABLE pdf_review_politeness_session (
 session_id TEXT PRIMARY KEY NOT NULL,
 fulltext_run_id TEXT NOT NULL REFERENCES fulltext_run(run_id),
 settings_json TEXT NOT NULL CHECK(json_valid(settings_json)),
 settings_sha256 TEXT NOT NULL CHECK(settings_sha256=foundation_sha256_text(settings_json)),
 started_at_utc TEXT NOT NULL
);
CREATE TABLE pdf_review_robots_request (
 request_id TEXT PRIMARY KEY NOT NULL,
 session_id TEXT NOT NULL REFERENCES pdf_review_politeness_session(session_id),
 host TEXT NOT NULL,
 reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes>=0),
 request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 request_sha256 TEXT NOT NULL CHECK(request_sha256=foundation_sha256_text(request_json)),
 created_at_utc TEXT NOT NULL
);
CREATE TABLE pdf_review_robots_result (
 request_id TEXT PRIMARY KEY NOT NULL REFERENCES pdf_review_robots_request(request_id),
 bytes INTEGER NOT NULL CHECK(bytes>=0),
 result_json TEXT NOT NULL CHECK(json_valid(result_json)),
 result_sha256 TEXT NOT NULL CHECK(result_sha256=foundation_sha256_text(result_json)),
 finished_at_utc TEXT NOT NULL
);
CREATE INDEX pdf_input_record_batch ON pdf_input_record(run_id,batch_id,row_ordinal);
CREATE INDEX pdf_candidate_work ON pdf_candidate(run_id,work_id,rank);
CREATE INDEX pdf_attempt_candidate ON pdf_attempt(run_id,candidate_id,ordinal);
CREATE INDEX pdf_work_artifact_work ON pdf_work_artifact(run_id,work_id);
CREATE INDEX pdf_manual_import_session ON pdf_manual_import(session_id,ordinal);
CREATE INDEX pdf_import_session_context ON pdf_import_session(context_kind,context_id);
CREATE INDEX pdf_host_event_host ON pdf_host_event(host,seq);
CREATE TRIGGER pdf_run_insert BEFORE INSERT ON pdf_run BEGIN
 SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR NEW.state!='OPEN' OR EXISTS(SELECT 1 FROM pdf_run WHERE run_id=NEW.run_id) THEN RAISE(ABORT,'immutable pdf run') END;
END;
CREATE TRIGGER pdf_run_delete BEFORE DELETE ON pdf_run BEGIN SELECT RAISE(ABORT,'immutable pdf run'); END;
CREATE TRIGGER pdf_run_update BEFORE UPDATE ON pdf_run BEGIN
 SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR NEW.run_id IS NOT OLD.run_id OR NEW.profile IS NOT OLD.profile OR NEW.label IS NOT OLD.label OR NEW.policy_json IS NOT OLD.policy_json OR NEW.policy_sha256 IS NOT OLD.policy_sha256 OR NEW.created_at_utc IS NOT OLD.created_at_utc
  OR NOT ((OLD.state='OPEN' AND NEW.state='DISCOVERING')
       OR (OLD.state='DISCOVERING' AND NEW.state='ACQUIRING' AND NEW.identity_json IS OLD.identity_json)
       OR (OLD.state='ACQUIRING' AND NEW.state='CLOSED' AND NEW.identity_json IS OLD.identity_json))
 THEN RAISE(ABORT,'immutable pdf run transition') END;
END;
CREATE TRIGGER pdf_input_batch_insert BEFORE INSERT ON pdf_input_batch BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR (SELECT state FROM pdf_run WHERE run_id=NEW.run_id) IS NOT 'OPEN' OR EXISTS(SELECT 1 FROM pdf_input_batch WHERE batch_id=NEW.batch_id) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_input_record_insert BEFORE INSERT ON pdf_input_record BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR (SELECT state FROM pdf_run WHERE run_id=NEW.run_id) IS NOT 'OPEN' OR NOT EXISTS(SELECT 1 FROM pdf_input_batch WHERE batch_id=NEW.batch_id AND run_id=NEW.run_id) OR EXISTS(SELECT 1 FROM pdf_input_record WHERE run_id=NEW.run_id AND record_id=NEW.record_id) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_work_insert BEFORE INSERT ON pdf_work BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR (SELECT state FROM pdf_run WHERE run_id=NEW.run_id) IS NOT 'DISCOVERING' OR EXISTS(SELECT 1 FROM pdf_work WHERE run_id=NEW.run_id AND work_id=NEW.work_id) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_evidence_insert BEFORE INSERT ON pdf_evidence BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR (SELECT state FROM pdf_run WHERE run_id=NEW.run_id) IS NOT 'DISCOVERING' OR EXISTS(SELECT 1 FROM pdf_evidence WHERE run_id=NEW.run_id AND evidence_id=NEW.evidence_id) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_plan_insert BEFORE INSERT ON pdf_plan BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR (SELECT state FROM pdf_run WHERE run_id=NEW.run_id) IS NOT 'DISCOVERING' OR EXISTS(SELECT 1 FROM pdf_plan WHERE run_id=NEW.run_id AND work_id=NEW.work_id) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_candidate_insert BEFORE INSERT ON pdf_candidate BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR (SELECT state FROM pdf_run WHERE run_id=NEW.run_id) IS NOT 'DISCOVERING' OR EXISTS(SELECT 1 FROM pdf_candidate WHERE run_id=NEW.run_id AND candidate_id=NEW.candidate_id) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_request_insert BEFORE INSERT ON pdf_request BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR NOT EXISTS(SELECT 1 FROM pdf_run WHERE run_id=NEW.run_id AND state IN ('DISCOVERING','ACQUIRING')) OR EXISTS(SELECT 1 FROM pdf_request WHERE run_id=NEW.run_id AND request_id=NEW.request_id) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_request_result_insert BEFORE INSERT ON pdf_request_result BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR NOT EXISTS(SELECT 1 FROM pdf_run WHERE run_id=NEW.run_id AND state IN ('DISCOVERING','ACQUIRING')) OR EXISTS(SELECT 1 FROM pdf_request_result WHERE run_id=NEW.run_id AND request_id=NEW.request_id)
  OR NOT EXISTS(SELECT 1 FROM pdf_request WHERE run_id=NEW.run_id AND request_id=NEW.request_id AND reserved_bytes>=NEW.bytes)
  OR typeof(NEW.bytes)!='integer' OR json_type(NEW.result_json,'$.bytes') IS NOT 'integer'
  OR NEW.bytes IS NOT json_extract(NEW.result_json,'$.bytes')
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_artifact_insert BEFORE INSERT ON pdf_artifact BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR EXISTS(SELECT 1 FROM pdf_artifact WHERE artifact_sha256=NEW.artifact_sha256) OR NOT EXISTS(SELECT 1 FROM raw_artifact WHERE artifact_sha256=NEW.artifact_sha256 AND byte_size=NEW.byte_size) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_attempt_insert BEFORE INSERT ON pdf_attempt BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR (SELECT state FROM pdf_run WHERE run_id=NEW.run_id) IS NOT 'ACQUIRING' OR NOT EXISTS(SELECT 1 FROM pdf_candidate WHERE run_id=NEW.run_id AND candidate_id=NEW.candidate_id AND eligible=1) OR EXISTS(SELECT 1 FROM pdf_attempt WHERE run_id=NEW.run_id AND attempt_id=NEW.attempt_id) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_attempt_result_insert BEFORE INSERT ON pdf_attempt_result BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR (SELECT state FROM pdf_run WHERE run_id=NEW.run_id) IS NOT 'ACQUIRING' OR EXISTS(SELECT 1 FROM pdf_attempt_result WHERE run_id=NEW.run_id AND attempt_id=NEW.attempt_id) THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_import_session_insert BEFORE INSERT ON pdf_import_session BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR EXISTS(SELECT 1 FROM pdf_import_session WHERE session_id=NEW.session_id)
  OR (NEW.context_kind='DISCOVERY_RUN' AND NOT EXISTS(SELECT 1 FROM pdf_run WHERE run_id=NEW.context_id AND state IN ('DISCOVERING','ACQUIRING')))
  OR (NEW.context_kind='REVIEW_FULLTEXT_RUN' AND NOT EXISTS(SELECT 1 FROM fulltext_run WHERE run_id=NEW.context_id))
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_manual_import_insert BEFORE INSERT ON pdf_manual_import BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR EXISTS(SELECT 1 FROM pdf_manual_import WHERE import_id=NEW.import_id)
  OR NOT EXISTS(SELECT 1 FROM pdf_import_session s WHERE s.session_id=NEW.session_id AND ((s.context_kind='DISCOVERY_RUN' AND EXISTS(SELECT 1 FROM pdf_run p WHERE p.run_id=s.context_id AND p.state IN ('DISCOVERING','ACQUIRING'))) OR (s.context_kind='REVIEW_FULLTEXT_RUN' AND EXISTS(SELECT 1 FROM fulltext_run f WHERE f.run_id=s.context_id))))
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_work_artifact_insert BEFORE INSERT ON pdf_work_artifact BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR NOT EXISTS(SELECT 1 FROM pdf_run WHERE run_id=NEW.run_id AND state IN ('DISCOVERING','ACQUIRING')) OR EXISTS(SELECT 1 FROM pdf_work_artifact WHERE link_id=NEW.link_id)
  OR (NEW.source='AUTO' AND NOT EXISTS(SELECT 1 FROM pdf_attempt_result WHERE run_id=NEW.run_id AND attempt_id=NEW.attempt_id AND outcome='ACQUIRED' AND artifact_sha256=NEW.artifact_sha256))
  OR (NEW.source!='AUTO' AND NOT EXISTS(SELECT 1 FROM pdf_manual_import m JOIN pdf_import_session s ON s.session_id=m.session_id WHERE m.import_id=NEW.import_id AND m.artifact_sha256=NEW.artifact_sha256 AND s.context_kind='DISCOVERY_RUN' AND s.context_id=NEW.run_id))
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_review_supplement_insert BEFORE INSERT ON pdf_review_supplement BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR EXISTS(SELECT 1 FROM pdf_review_supplement WHERE link_id=NEW.link_id)
  OR NOT EXISTS(SELECT 1 FROM pdf_manual_import m JOIN pdf_import_session s ON s.session_id=m.session_id WHERE m.import_id=NEW.import_id AND m.artifact_sha256=NEW.artifact_sha256 AND s.context_kind='REVIEW_FULLTEXT_RUN' AND s.context_id=NEW.fulltext_run_id)
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_reconciliation_insert BEFORE INSERT ON pdf_reconciliation BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR EXISTS(SELECT 1 FROM pdf_reconciliation WHERE item_id=NEW.item_id)
  OR NOT EXISTS(SELECT 1 FROM pdf_manual_import m JOIN pdf_import_session s ON s.session_id=m.session_id WHERE m.import_id=NEW.import_id AND m.artifact_sha256 IS NOT NULL AND s.context_kind=NEW.context_kind AND s.context_id=NEW.context_id)
  OR NOT EXISTS(SELECT 1 FROM pdf_manual_import m WHERE m.import_id=NEW.import_id AND EXISTS(SELECT 1 FROM pdf_import_session s WHERE s.session_id=m.session_id AND ((s.context_kind='DISCOVERY_RUN' AND EXISTS(SELECT 1 FROM pdf_run p WHERE p.run_id=s.context_id AND p.state IN ('DISCOVERING','ACQUIRING'))) OR (s.context_kind='REVIEW_FULLTEXT_RUN' AND EXISTS(SELECT 1 FROM fulltext_run f WHERE f.run_id=s.context_id)))))
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_reconciliation_decision_insert BEFORE INSERT ON pdf_reconciliation_decision BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 OR EXISTS(SELECT 1 FROM pdf_reconciliation_decision WHERE item_id=NEW.item_id)
  OR NOT EXISTS(SELECT 1 FROM pdf_reconciliation r WHERE r.item_id=NEW.item_id AND ((r.context_kind='DISCOVERY_RUN' AND EXISTS(SELECT 1 FROM pdf_run p WHERE p.run_id=r.context_id AND p.state IN ('DISCOVERING','ACQUIRING')))
     OR (r.context_kind='REVIEW_FULLTEXT_RUN' AND EXISTS(SELECT 1 FROM fulltext_run f WHERE f.run_id=r.context_id))))
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_host_event_insert BEFORE INSERT ON pdf_host_event BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1 THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_review_politeness_session_insert BEFORE INSERT ON pdf_review_politeness_session BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1
  OR EXISTS(SELECT 1 FROM pdf_review_politeness_session WHERE session_id=NEW.session_id)
  OR NOT EXISTS(SELECT 1 FROM fulltext_run WHERE run_id=NEW.fulltext_run_id)
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_review_robots_request_insert BEFORE INSERT ON pdf_review_robots_request BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1
  OR EXISTS(SELECT 1 FROM pdf_review_robots_request WHERE request_id=NEW.request_id)
  OR NOT EXISTS(SELECT 1 FROM pdf_review_politeness_session WHERE session_id=NEW.session_id)
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_review_robots_result_insert BEFORE INSERT ON pdf_review_robots_result BEGIN SELECT CASE WHEN pdf_acquisition_authorized()!=1
  OR EXISTS(SELECT 1 FROM pdf_review_robots_result WHERE request_id=NEW.request_id)
  OR NOT EXISTS(SELECT 1 FROM pdf_review_robots_request WHERE request_id=NEW.request_id AND reserved_bytes>=NEW.bytes)
  OR typeof(NEW.bytes)!='integer' OR json_type(NEW.result_json,'$.bytes') IS NOT 'integer'
  OR NEW.bytes IS NOT json_extract(NEW.result_json,'$.bytes')
  THEN RAISE(ABORT,'immutable pdf insert') END; END;
CREATE TRIGGER pdf_review_robots_result_update BEFORE UPDATE ON pdf_review_robots_result BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_review_robots_result_delete BEFORE DELETE ON pdf_review_robots_result BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_review_politeness_session_update BEFORE UPDATE ON pdf_review_politeness_session BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_review_politeness_session_delete BEFORE DELETE ON pdf_review_politeness_session BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_review_robots_request_update BEFORE UPDATE ON pdf_review_robots_request BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_review_robots_request_delete BEFORE DELETE ON pdf_review_robots_request BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_input_batch_update BEFORE UPDATE ON pdf_input_batch BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_input_batch_delete BEFORE DELETE ON pdf_input_batch BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_input_record_update BEFORE UPDATE ON pdf_input_record BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_input_record_delete BEFORE DELETE ON pdf_input_record BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_work_update BEFORE UPDATE ON pdf_work BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_work_delete BEFORE DELETE ON pdf_work BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_evidence_update BEFORE UPDATE ON pdf_evidence BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_evidence_delete BEFORE DELETE ON pdf_evidence BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_plan_update BEFORE UPDATE ON pdf_plan BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_plan_delete BEFORE DELETE ON pdf_plan BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_candidate_update BEFORE UPDATE ON pdf_candidate BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_candidate_delete BEFORE DELETE ON pdf_candidate BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_request_update BEFORE UPDATE ON pdf_request BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_request_delete BEFORE DELETE ON pdf_request BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_request_result_update BEFORE UPDATE ON pdf_request_result BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_request_result_delete BEFORE DELETE ON pdf_request_result BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_artifact_update BEFORE UPDATE ON pdf_artifact BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_artifact_delete BEFORE DELETE ON pdf_artifact BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_attempt_update BEFORE UPDATE ON pdf_attempt BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_attempt_delete BEFORE DELETE ON pdf_attempt BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_attempt_result_update BEFORE UPDATE ON pdf_attempt_result BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_attempt_result_delete BEFORE DELETE ON pdf_attempt_result BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_import_session_update BEFORE UPDATE ON pdf_import_session BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_import_session_delete BEFORE DELETE ON pdf_import_session BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_manual_import_update BEFORE UPDATE ON pdf_manual_import BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_manual_import_delete BEFORE DELETE ON pdf_manual_import BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_work_artifact_update BEFORE UPDATE ON pdf_work_artifact BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_work_artifact_delete BEFORE DELETE ON pdf_work_artifact BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_review_supplement_update BEFORE UPDATE ON pdf_review_supplement BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_review_supplement_delete BEFORE DELETE ON pdf_review_supplement BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_reconciliation_update BEFORE UPDATE ON pdf_reconciliation BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_reconciliation_delete BEFORE DELETE ON pdf_reconciliation BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_reconciliation_decision_update BEFORE UPDATE ON pdf_reconciliation_decision BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_reconciliation_decision_delete BEFORE DELETE ON pdf_reconciliation_decision BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_host_event_update BEFORE UPDATE ON pdf_host_event BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;
CREATE TRIGGER pdf_host_event_delete BEFORE DELETE ON pdf_host_event BEGIN SELECT RAISE(ABORT,'immutable pdf row'); END;

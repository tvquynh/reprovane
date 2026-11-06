-- Stage11B acquisition only; no Stage12 release semantics.
CREATE TABLE fulltext_run (
 run_id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES corpus_release(release_id),
 release_manifest_sha256 TEXT NOT NULL, policy_json TEXT NOT NULL, policy_sha256 TEXT NOT NULL,
 handoff_json TEXT NOT NULL, handoff_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('OPEN','ACQUIRING','MATERIALIZED','SEALED')),
 manifest_json TEXT, manifest_sha256 TEXT, created_at TEXT NOT NULL,
 UNIQUE(release_id,policy_sha256)
);
CREATE TABLE fulltext_input (
 run_id TEXT NOT NULL REFERENCES fulltext_run(run_id), corpus_item_id TEXT NOT NULL REFERENCES corpus_release_item(corpus_item_id),
 input_json TEXT NOT NULL, input_sha256 TEXT NOT NULL, PRIMARY KEY(run_id,corpus_item_id)
);
CREATE TABLE fulltext_evidence (
 run_id TEXT NOT NULL REFERENCES fulltext_run(run_id), evidence_id TEXT NOT NULL,
 provider TEXT NOT NULL, linkage TEXT NOT NULL, response_sha256 TEXT, metadata_json TEXT NOT NULL,
 metadata_sha256 TEXT NOT NULL, retrieved_at TEXT NOT NULL,
 PRIMARY KEY(run_id,evidence_id), UNIQUE(run_id,provider,linkage)
);
CREATE TABLE fulltext_plan (
 run_id TEXT NOT NULL, corpus_item_id TEXT NOT NULL, plan_json TEXT NOT NULL, plan_sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,corpus_item_id), FOREIGN KEY(run_id,corpus_item_id) REFERENCES fulltext_input(run_id,corpus_item_id)
);
CREATE TABLE fulltext_candidate (
 run_id TEXT NOT NULL, corpus_item_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
 candidate_json TEXT NOT NULL, candidate_sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,candidate_id), FOREIGN KEY(run_id,corpus_item_id) REFERENCES fulltext_plan(run_id,corpus_item_id)
);
CREATE TABLE fulltext_request (
 run_id TEXT NOT NULL REFERENCES fulltext_run(run_id), request_id TEXT NOT NULL,
 request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,request_id)
);
CREATE TABLE fulltext_request_result (
 run_id TEXT NOT NULL, request_id TEXT NOT NULL, result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL,
 finished_at TEXT NOT NULL, PRIMARY KEY(run_id,request_id),
 FOREIGN KEY(run_id,request_id) REFERENCES fulltext_request(run_id,request_id)
);
CREATE TABLE fulltext_attempt (
 run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, attempt_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
 started_at TEXT NOT NULL, PRIMARY KEY(run_id,attempt_id), UNIQUE(run_id,candidate_id,ordinal),
 FOREIGN KEY(run_id,candidate_id) REFERENCES fulltext_candidate(run_id,candidate_id)
);
CREATE TABLE fulltext_artifact (
 run_id TEXT NOT NULL REFERENCES fulltext_run(run_id), artifact_sha256 TEXT NOT NULL,
 artifact_json TEXT NOT NULL, metadata_sha256 TEXT NOT NULL, first_acquired_at TEXT NOT NULL,
 PRIMARY KEY(run_id,artifact_sha256)
);
CREATE TABLE fulltext_attempt_result (
 run_id TEXT NOT NULL, attempt_id TEXT NOT NULL, result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL,
 artifact_sha256 TEXT, finished_at TEXT NOT NULL,
 PRIMARY KEY(run_id,attempt_id), FOREIGN KEY(run_id,attempt_id) REFERENCES fulltext_attempt(run_id,attempt_id),
 FOREIGN KEY(run_id,artifact_sha256) REFERENCES fulltext_artifact(run_id,artifact_sha256)
);
CREATE TABLE fulltext_status (
 run_id TEXT NOT NULL, corpus_item_id TEXT NOT NULL, status_json TEXT NOT NULL, status_sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,corpus_item_id), FOREIGN KEY(run_id,corpus_item_id) REFERENCES fulltext_input(run_id,corpus_item_id)
);
CREATE TRIGGER fulltext_source BEFORE INSERT ON fulltext_input BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM fulltext_run r JOIN corpus_release_item i ON i.release_id=r.release_id WHERE r.run_id=NEW.run_id AND i.corpus_item_id=NEW.corpus_item_id) THEN RAISE(ABORT,'cross-release fulltext input') END;
END;
CREATE TRIGGER fulltext_run_insert BEFORE INSERT ON fulltext_run BEGIN
 SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR NEW.state!='OPEN' OR EXISTS(SELECT 1 FROM fulltext_run WHERE run_id=NEW.run_id OR (release_id=NEW.release_id AND policy_sha256=NEW.policy_sha256)) OR NOT EXISTS(SELECT 1 FROM corpus_release WHERE release_id=NEW.release_id AND state='SEALED' AND release_manifest_sha256=NEW.release_manifest_sha256) THEN RAISE(ABORT,'immutable fulltext run') END;
END;
CREATE TRIGGER fulltext_run_delete BEFORE DELETE ON fulltext_run BEGIN SELECT RAISE(ABORT,'immutable fulltext run'); END;
CREATE TRIGGER fulltext_run_update BEFORE UPDATE ON fulltext_run BEGIN
 SELECT CASE WHEN fulltext_authorized(OLD.run_id)!=1 OR OLD.run_id!=NEW.run_id OR OLD.release_id!=NEW.release_id OR OLD.release_manifest_sha256!=NEW.release_manifest_sha256 OR OLD.policy_json!=NEW.policy_json OR OLD.policy_sha256!=NEW.policy_sha256 OR OLD.handoff_json!=NEW.handoff_json OR OLD.handoff_sha256!=NEW.handoff_sha256 OR OLD.created_at!=NEW.created_at OR NOT ((OLD.state='OPEN' AND NEW.state='ACQUIRING' AND NEW.manifest_json IS NULL AND NEW.manifest_sha256 IS NULL) OR (OLD.state='ACQUIRING' AND NEW.state='MATERIALIZED' AND NEW.manifest_json IS NOT NULL AND NEW.manifest_sha256 IS NOT NULL) OR (OLD.state='MATERIALIZED' AND NEW.state='SEALED' AND OLD.manifest_json=NEW.manifest_json AND OLD.manifest_sha256=NEW.manifest_sha256)) THEN RAISE(ABORT,'immutable fulltext transition') END;
END;
CREATE TRIGGER fulltext_input_insert BEFORE INSERT ON fulltext_input BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('OPEN') OR EXISTS(SELECT 1 FROM fulltext_input WHERE run_id=NEW.run_id AND corpus_item_id=NEW.corpus_item_id) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_input_update BEFORE UPDATE ON fulltext_input BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_input_delete BEFORE DELETE ON fulltext_input BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_evidence_insert BEFORE INSERT ON fulltext_evidence BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('OPEN') OR EXISTS(SELECT 1 FROM fulltext_evidence WHERE run_id=NEW.run_id AND evidence_id=NEW.evidence_id) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_evidence_update BEFORE UPDATE ON fulltext_evidence BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_evidence_delete BEFORE DELETE ON fulltext_evidence BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_plan_insert BEFORE INSERT ON fulltext_plan BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('OPEN') OR EXISTS(SELECT 1 FROM fulltext_plan WHERE run_id=NEW.run_id AND corpus_item_id=NEW.corpus_item_id) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_plan_update BEFORE UPDATE ON fulltext_plan BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_plan_delete BEFORE DELETE ON fulltext_plan BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_candidate_insert BEFORE INSERT ON fulltext_candidate BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('OPEN') OR EXISTS(SELECT 1 FROM fulltext_candidate WHERE run_id=NEW.run_id AND candidate_id=NEW.candidate_id) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_candidate_update BEFORE UPDATE ON fulltext_candidate BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_candidate_delete BEFORE DELETE ON fulltext_candidate BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_request_insert BEFORE INSERT ON fulltext_request BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('OPEN','ACQUIRING') OR EXISTS(SELECT 1 FROM fulltext_request WHERE run_id=NEW.run_id AND request_id=NEW.request_id) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_request_update BEFORE UPDATE ON fulltext_request BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_request_delete BEFORE DELETE ON fulltext_request BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_request_result_insert BEFORE INSERT ON fulltext_request_result BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('OPEN','ACQUIRING') OR EXISTS(SELECT 1 FROM fulltext_request_result WHERE run_id=NEW.run_id AND request_id=NEW.request_id) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_request_result_update BEFORE UPDATE ON fulltext_request_result BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_request_result_delete BEFORE DELETE ON fulltext_request_result BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_attempt_insert BEFORE INSERT ON fulltext_attempt BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('ACQUIRING') OR EXISTS(SELECT 1 FROM fulltext_attempt WHERE run_id=NEW.run_id AND attempt_id=NEW.attempt_id) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_attempt_update BEFORE UPDATE ON fulltext_attempt BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_attempt_delete BEFORE DELETE ON fulltext_attempt BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_artifact_insert BEFORE INSERT ON fulltext_artifact BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('ACQUIRING') OR EXISTS(SELECT 1 FROM fulltext_artifact WHERE run_id=NEW.run_id AND artifact_sha256=NEW.artifact_sha256) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_artifact_update BEFORE UPDATE ON fulltext_artifact BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_artifact_delete BEFORE DELETE ON fulltext_artifact BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_attempt_result_insert BEFORE INSERT ON fulltext_attempt_result BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('ACQUIRING') OR EXISTS(SELECT 1 FROM fulltext_attempt_result WHERE run_id=NEW.run_id AND attempt_id=NEW.attempt_id) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_attempt_result_update BEFORE UPDATE ON fulltext_attempt_result BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_attempt_result_delete BEFORE DELETE ON fulltext_attempt_result BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_status_insert BEFORE INSERT ON fulltext_status BEGIN SELECT CASE WHEN fulltext_authorized(NEW.run_id)!=1 OR (SELECT state FROM fulltext_run WHERE run_id=NEW.run_id) NOT IN ('ACQUIRING') OR EXISTS(SELECT 1 FROM fulltext_status WHERE run_id=NEW.run_id AND corpus_item_id=NEW.corpus_item_id) THEN RAISE(ABORT,'immutable fulltext insert') END; END;
CREATE TRIGGER fulltext_status_update BEFORE UPDATE ON fulltext_status BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;
CREATE TRIGGER fulltext_status_delete BEFORE DELETE ON fulltext_status BEGIN SELECT RAISE(ABORT,'immutable fulltext row'); END;

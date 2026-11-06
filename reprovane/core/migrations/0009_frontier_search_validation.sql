CREATE TABLE frontier_run (
 frontier_run_id TEXT PRIMARY KEY NOT NULL, project_revision_id TEXT NOT NULL REFERENCES project_revision(revision_id),
 discovery_run_id TEXT NOT NULL UNIQUE REFERENCES discovery_run(discovery_run_id),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)), policy_sha256 TEXT NOT NULL CHECK(policy_sha256=foundation_sha256_text(policy_json)),
 initial_json TEXT NOT NULL CHECK(json_valid(initial_json)),
 state TEXT NOT NULL CHECK(state IN ('OPEN','FAILED','STOPPED','SEALED')), stop_reason TEXT,
 finalization_sha256 TEXT, created_at_utc TEXT NOT NULL, sealed_at_utc TEXT,
 CHECK((state='OPEN')=(stop_reason IS NULL)), CHECK((state='SEALED')=(finalization_sha256 IS NOT NULL)), CHECK((state='SEALED')=(sealed_at_utc IS NOT NULL))
);
CREATE TABLE frontier_round (
 frontier_run_id TEXT NOT NULL REFERENCES frontier_run(frontier_run_id), round_number INTEGER NOT NULL CHECK(round_number>0),
 plan_json TEXT NOT NULL CHECK(json_valid(plan_json)), plan_sha256 TEXT NOT NULL CHECK(plan_sha256=foundation_sha256_text(plan_json)),
 state TEXT NOT NULL CHECK(state IN ('PLANNED','FAILED','COMPLETE')), result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
 created_at_utc TEXT NOT NULL, completed_at_utc TEXT,
 PRIMARY KEY(frontier_run_id,round_number), CHECK((state='COMPLETE')=(result_json IS NOT NULL)), CHECK((state='COMPLETE')=(completed_at_utc IS NOT NULL))
);
CREATE TABLE frontier_action (
 frontier_run_id TEXT NOT NULL, round_number INTEGER NOT NULL, action_index INTEGER NOT NULL CHECK(action_index>=0),
 operation_id TEXT NOT NULL REFERENCES discovery_operation(operation_id), receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)), created_at_utc TEXT NOT NULL,
 PRIMARY KEY(frontier_run_id,round_number,action_index), UNIQUE(frontier_run_id,operation_id),
 FOREIGN KEY(frontier_run_id,round_number) REFERENCES frontier_round(frontier_run_id,round_number)
);
CREATE TABLE search_validation_run (
 validation_run_id TEXT PRIMARY KEY NOT NULL, project_revision_id TEXT NOT NULL REFERENCES project_revision(revision_id),
 plan_json TEXT NOT NULL CHECK(json_valid(plan_json)), plan_sha256 TEXT NOT NULL CHECK(plan_sha256=foundation_sha256_text(plan_json)),
 state TEXT NOT NULL CHECK(state IN ('OPEN','FAILED','COMPLETE','SEALED')), finalization_sha256 TEXT, created_at_utc TEXT NOT NULL, sealed_at_utc TEXT,
 CHECK((state='SEALED')=(finalization_sha256 IS NOT NULL)), CHECK((state='SEALED')=(sealed_at_utc IS NOT NULL))
);
CREATE TABLE search_validation_variant (
 validation_run_id TEXT NOT NULL REFERENCES search_validation_run(validation_run_id), label TEXT NOT NULL,
 acquisition_id TEXT NOT NULL REFERENCES openalex_acquisition(acquisition_id), request_spec_sha256 TEXT NOT NULL,
 PRIMARY KEY(validation_run_id,label)
);
CREATE TABLE search_validation_sentinel (
 validation_run_id TEXT NOT NULL REFERENCES search_validation_run(validation_run_id), sentinel_id TEXT NOT NULL,
 definition_json TEXT NOT NULL CHECK(json_valid(definition_json)), PRIMARY KEY(validation_run_id,sentinel_id)
);
CREATE TABLE search_validation_result (
 validation_run_id TEXT PRIMARY KEY NOT NULL REFERENCES search_validation_run(validation_run_id), resolution_run_id TEXT REFERENCES resolution_run(resolution_run_id),
 metrics_json TEXT NOT NULL CHECK(json_valid(metrics_json)), metrics_sha256 TEXT NOT NULL CHECK(metrics_sha256=foundation_sha256_text(metrics_json)), created_at_utc TEXT NOT NULL
);

CREATE TRIGGER frontier_run_insert BEFORE INSERT ON frontier_run BEGIN
 SELECT CASE WHEN stage09_authorized('F',NEW.frontier_run_id)!=1 OR EXISTS(SELECT 1 FROM frontier_run WHERE frontier_run_id=NEW.frontier_run_id OR discovery_run_id=NEW.discovery_run_id) THEN RAISE(ABORT,'immutable Stage09 identity') END;
 SELECT CASE WHEN NEW.state!='OPEN' OR NOT EXISTS(SELECT 1 FROM discovery_run WHERE discovery_run_id=NEW.discovery_run_id AND project_revision_id=NEW.project_revision_id AND state='OPEN') THEN RAISE(ABORT,'frontier requires open same-revision discovery') END;
END;
CREATE TRIGGER frontier_run_delete BEFORE DELETE ON frontier_run BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;
CREATE TRIGGER frontier_run_update BEFORE UPDATE ON frontier_run BEGIN SELECT CASE WHEN OLD.state='SEALED' OR stage09_authorized('F',OLD.frontier_run_id)!=1 OR NEW.frontier_run_id IS NOT OLD.frontier_run_id OR NEW.project_revision_id IS NOT OLD.project_revision_id OR NEW.created_at_utc IS NOT OLD.created_at_utc OR NEW.discovery_run_id IS NOT OLD.discovery_run_id OR NEW.policy_json IS NOT OLD.policy_json OR NEW.policy_sha256 IS NOT OLD.policy_sha256 OR NEW.initial_json IS NOT OLD.initial_json THEN RAISE(ABORT,'immutable Stage09 run') END; END;

CREATE TRIGGER frontier_round_insert BEFORE INSERT ON frontier_round BEGIN
 SELECT CASE WHEN stage09_authorized('F',NEW.frontier_run_id)!=1 OR EXISTS(SELECT 1 FROM frontier_round WHERE frontier_run_id=NEW.frontier_run_id AND round_number=NEW.round_number) THEN RAISE(ABORT,'immutable Stage09 identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM frontier_run f JOIN discovery_run d ON d.discovery_run_id=f.discovery_run_id WHERE f.frontier_run_id=NEW.frontier_run_id AND f.state IN ('OPEN','FAILED') AND d.state='OPEN') THEN RAISE(ABORT,'frontier locked') END;
 SELECT CASE WHEN NEW.state!='PLANNED' THEN RAISE(ABORT,'round must start planned') END;
END;
CREATE TRIGGER frontier_round_delete BEFORE DELETE ON frontier_round BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;
CREATE TRIGGER frontier_round_update BEFORE UPDATE ON frontier_round BEGIN SELECT CASE WHEN OLD.state='COMPLETE' OR NEW.frontier_run_id IS NOT OLD.frontier_run_id OR NEW.round_number IS NOT OLD.round_number OR NEW.plan_json IS NOT OLD.plan_json OR NEW.plan_sha256 IS NOT OLD.plan_sha256 OR NEW.created_at_utc IS NOT OLD.created_at_utc OR stage09_authorized('F',OLD.frontier_run_id)!=1 THEN RAISE(ABORT,'immutable frontier round') END; END;

CREATE TRIGGER frontier_action_insert BEFORE INSERT ON frontier_action BEGIN
 SELECT CASE WHEN stage09_authorized('F',NEW.frontier_run_id)!=1 OR EXISTS(SELECT 1 FROM frontier_action WHERE frontier_run_id=NEW.frontier_run_id AND ((round_number=NEW.round_number AND action_index=NEW.action_index) OR operation_id=NEW.operation_id)) THEN RAISE(ABORT,'immutable Stage09 identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM frontier_run f JOIN discovery_run d ON d.discovery_run_id=f.discovery_run_id WHERE f.frontier_run_id=NEW.frontier_run_id AND f.state IN ('OPEN','FAILED') AND d.state='OPEN') THEN RAISE(ABORT,'frontier locked') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM frontier_round r JOIN frontier_run f ON f.frontier_run_id=r.frontier_run_id JOIN discovery_operation o ON o.discovery_run_id=f.discovery_run_id WHERE r.frontier_run_id=NEW.frontier_run_id AND r.round_number=NEW.round_number AND r.state!='COMPLETE' AND o.operation_id=NEW.operation_id AND o.state='COMPLETE') THEN RAISE(ABORT,'frontier action context mismatch') END;
END;
CREATE TRIGGER frontier_action_delete BEFORE DELETE ON frontier_action BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;
CREATE TRIGGER frontier_action_update BEFORE UPDATE ON frontier_action BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;

CREATE TRIGGER search_validation_run_insert BEFORE INSERT ON search_validation_run BEGIN
 SELECT CASE WHEN stage09_authorized('V',NEW.validation_run_id)!=1 OR EXISTS(SELECT 1 FROM search_validation_run WHERE validation_run_id=NEW.validation_run_id) THEN RAISE(ABORT,'immutable Stage09 identity') END;
 SELECT CASE WHEN NEW.state!='OPEN' THEN RAISE(ABORT,'validation must start open') END;
END;
CREATE TRIGGER search_validation_run_delete BEFORE DELETE ON search_validation_run BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;
CREATE TRIGGER search_validation_run_update BEFORE UPDATE ON search_validation_run BEGIN SELECT CASE WHEN OLD.state='SEALED' OR stage09_authorized('V',OLD.validation_run_id)!=1 OR NEW.validation_run_id IS NOT OLD.validation_run_id OR NEW.project_revision_id IS NOT OLD.project_revision_id OR NEW.created_at_utc IS NOT OLD.created_at_utc OR NEW.plan_json IS NOT OLD.plan_json OR NEW.plan_sha256 IS NOT OLD.plan_sha256 THEN RAISE(ABORT,'immutable Stage09 run') END; END;

CREATE TRIGGER search_validation_variant_insert BEFORE INSERT ON search_validation_variant BEGIN
 SELECT CASE WHEN stage09_authorized('V',NEW.validation_run_id)!=1 OR EXISTS(SELECT 1 FROM search_validation_variant WHERE validation_run_id=NEW.validation_run_id AND label=NEW.label) THEN RAISE(ABORT,'immutable Stage09 identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM search_validation_run WHERE validation_run_id=NEW.validation_run_id AND state IN ('OPEN','FAILED')) THEN RAISE(ABORT,'validation locked') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM openalex_acquisition a JOIN search_validation_run v ON v.project_revision_id=a.project_revision_id WHERE v.validation_run_id=NEW.validation_run_id AND a.acquisition_id=NEW.acquisition_id AND a.request_spec_sha256=NEW.request_spec_sha256) THEN RAISE(ABORT,'variant revision/spec mismatch') END;
END;
CREATE TRIGGER search_validation_variant_delete BEFORE DELETE ON search_validation_variant BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;
CREATE TRIGGER search_validation_variant_update BEFORE UPDATE ON search_validation_variant BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;

CREATE TRIGGER search_validation_sentinel_insert BEFORE INSERT ON search_validation_sentinel BEGIN
 SELECT CASE WHEN stage09_authorized('V',NEW.validation_run_id)!=1 OR EXISTS(SELECT 1 FROM search_validation_sentinel WHERE validation_run_id=NEW.validation_run_id AND sentinel_id=NEW.sentinel_id) THEN RAISE(ABORT,'immutable Stage09 identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM search_validation_run WHERE validation_run_id=NEW.validation_run_id AND state IN ('OPEN','FAILED')) THEN RAISE(ABORT,'validation locked') END;
END;
CREATE TRIGGER search_validation_sentinel_delete BEFORE DELETE ON search_validation_sentinel BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;
CREATE TRIGGER search_validation_sentinel_update BEFORE UPDATE ON search_validation_sentinel BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;

CREATE TRIGGER search_validation_result_insert BEFORE INSERT ON search_validation_result BEGIN
 SELECT CASE WHEN stage09_authorized('V',NEW.validation_run_id)!=1 OR EXISTS(SELECT 1 FROM search_validation_result WHERE validation_run_id=NEW.validation_run_id) THEN RAISE(ABORT,'immutable Stage09 identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM search_validation_run WHERE validation_run_id=NEW.validation_run_id AND state IN ('OPEN','FAILED')) THEN RAISE(ABORT,'validation locked') END;
END;
CREATE TRIGGER search_validation_result_delete BEFORE DELETE ON search_validation_result BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;
CREATE TRIGGER search_validation_result_update BEFORE UPDATE ON search_validation_result BEGIN SELECT RAISE(ABORT,'immutable Stage09 evidence'); END;

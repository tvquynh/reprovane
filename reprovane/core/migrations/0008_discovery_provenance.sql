CREATE TABLE discovery_run (
 discovery_run_id TEXT PRIMARY KEY NOT NULL,
 project_revision_id TEXT NOT NULL REFERENCES project_revision(revision_id),
 parent_run_id TEXT REFERENCES discovery_run(discovery_run_id),
 state TEXT NOT NULL CHECK(state IN ('OPEN','MATERIALIZED','SEALED')),
 resolution_run_id TEXT REFERENCES resolution_run(resolution_run_id),
 materialization_sha256 TEXT CHECK(materialization_sha256 IS NULL OR (length(materialization_sha256)=64 AND materialization_sha256 NOT GLOB '*[^0-9a-f]*')),
 created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z'),
 materialized_at_utc TEXT CHECK(materialized_at_utc IS NULL OR materialized_at_utc GLOB '*Z'),
 sealed_at_utc TEXT CHECK(sealed_at_utc IS NULL OR sealed_at_utc GLOB '*Z'),
 CHECK((state='OPEN')=(materialized_at_utc IS NULL)),
 CHECK((state='SEALED')=(sealed_at_utc IS NOT NULL)),
 CHECK((state='OPEN')=(materialization_sha256 IS NULL))
);
CREATE TABLE discovery_operation (
 operation_id TEXT PRIMARY KEY NOT NULL,
 discovery_run_id TEXT NOT NULL REFERENCES discovery_run(discovery_run_id),
 operation_kind TEXT NOT NULL CHECK(operation_kind IN ('PRIMARY_QUERY','BACKWARD_CITATION','FORWARD_CITATION','RESOLUTION_CHECKPOINT')),
 specification_json TEXT NOT NULL CHECK(json_valid(specification_json)),
 specification_sha256 TEXT NOT NULL CHECK(specification_sha256=foundation_sha256_text(specification_json)),
 state TEXT NOT NULL CHECK(state IN ('OPEN','FAILED','COMPLETE')),
 result_resolution_run_id TEXT REFERENCES resolution_run(resolution_run_id),
 statistics_json TEXT NOT NULL CHECK(json_valid(statistics_json)),
 plan_json TEXT CHECK(plan_json IS NULL OR json_valid(plan_json)),
 created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z'),
 completed_at_utc TEXT CHECK(completed_at_utc IS NULL OR completed_at_utc GLOB '*Z'),
 UNIQUE(discovery_run_id,specification_sha256),
 CHECK((state='COMPLETE')=(completed_at_utc IS NOT NULL))
);
CREATE TABLE discovery_operation_acquisition (
 operation_id TEXT NOT NULL REFERENCES discovery_operation(operation_id),
 acquisition_id TEXT NOT NULL REFERENCES openalex_acquisition(acquisition_id),
 role TEXT NOT NULL CHECK(role IN ('PRIMARY_SOURCE','SUPPLEMENTAL','TARGET','FORWARD')),
 PRIMARY KEY(operation_id,acquisition_id,role)
);
CREATE TABLE discovery_event (
 event_id TEXT PRIMARY KEY NOT NULL,
 discovery_run_id TEXT NOT NULL REFERENCES discovery_run(discovery_run_id),
 operation_id TEXT NOT NULL REFERENCES discovery_operation(operation_id),
 event_type TEXT NOT NULL CHECK(event_type IN ('PRIMARY_QUERY','BACKWARD_CITATION','FORWARD_CITATION')),
 acquisition_id TEXT REFERENCES openalex_acquisition(acquisition_id),
 provider_record_id TEXT REFERENCES provider_record(provider_record_id),
 requested_target_id TEXT,
 returned_work_id TEXT,
 seed_resolution_run_id TEXT,
 seed_entity_id TEXT,
 seed_record_id TEXT REFERENCES provider_record(provider_record_id),
 seed_work_id TEXT,
 seed_depth INTEGER,
 depth INTEGER NOT NULL CHECK(depth>=0),
 citation_observation_id TEXT REFERENCES citation_edge_observation(observation_id),
 metadata_state TEXT NOT NULL CHECK(metadata_state IN ('ACQUIRED','LOCAL','MISSING','MALFORMED','UNATTRIBUTED_ID_CHANGE','SELF_LOOP')),
 self_loop INTEGER NOT NULL CHECK(self_loop IN (0,1)),
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z'),
 FOREIGN KEY(seed_resolution_run_id,seed_entity_id) REFERENCES publication_entity(resolution_run_id,entity_id),
 CHECK((provider_record_id IS NULL)=(returned_work_id IS NULL)),
 CHECK((event_type='PRIMARY_QUERY' AND depth=0 AND seed_record_id IS NULL AND seed_entity_id IS NULL AND seed_resolution_run_id IS NULL AND seed_work_id IS NULL AND seed_depth IS NULL AND citation_observation_id IS NULL AND self_loop=0)
 OR (event_type!='PRIMARY_QUERY' AND seed_record_id IS NOT NULL AND seed_entity_id IS NOT NULL AND seed_resolution_run_id IS NOT NULL AND seed_work_id IS NOT NULL AND seed_depth>=0 AND depth=seed_depth+1))
);
CREATE INDEX discovery_event_record ON discovery_event(discovery_run_id,provider_record_id);
CREATE TABLE discovery_membership (
 discovery_run_id TEXT NOT NULL REFERENCES discovery_run(discovery_run_id),
 resolution_run_id TEXT NOT NULL,
 publication_entity_id TEXT NOT NULL,
 membership_basis_json TEXT NOT NULL CHECK(json_valid(membership_basis_json)),
 created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z'),
 PRIMARY KEY(discovery_run_id,publication_entity_id),
 FOREIGN KEY(resolution_run_id,publication_entity_id) REFERENCES publication_entity(resolution_run_id,entity_id)
);
CREATE TABLE discovery_attempt (
 attempt_id TEXT PRIMARY KEY NOT NULL,
 discovery_run_id TEXT NOT NULL REFERENCES discovery_run(discovery_run_id),
 operation_id TEXT NOT NULL REFERENCES discovery_operation(operation_id),
 suppressed_event_count INTEGER NOT NULL CHECK(suppressed_event_count>=0),
 created_at_utc TEXT NOT NULL CHECK(created_at_utc GLOB '*Z')
);
CREATE VIEW discovery_event_view AS
 SELECT e.*,m.publication_entity_id AS resolved_publication_entity_id
 FROM discovery_event e JOIN discovery_run r ON r.discovery_run_id=e.discovery_run_id
 LEFT JOIN entity_membership em ON em.resolution_run_id=r.resolution_run_id AND em.provider_record_id=e.provider_record_id
 LEFT JOIN discovery_membership m ON m.discovery_run_id=e.discovery_run_id AND m.publication_entity_id=em.entity_id;
CREATE TRIGGER discovery_run_insert BEFORE INSERT ON discovery_run BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM discovery_run WHERE discovery_run_id=NEW.discovery_run_id) THEN RAISE(ABORT,'immutable discovery run') END;
 SELECT CASE WHEN NEW.state!='OPEN' OR NEW.resolution_run_id IS NOT NULL THEN RAISE(ABORT,'discovery must start open') END;
END;
CREATE TRIGGER discovery_run_delete BEFORE DELETE ON discovery_run BEGIN SELECT RAISE(ABORT,'immutable discovery run'); END;
CREATE TRIGGER discovery_run_update BEFORE UPDATE ON discovery_run BEGIN
 SELECT CASE WHEN NEW.discovery_run_id IS NOT OLD.discovery_run_id OR NEW.project_revision_id IS NOT OLD.project_revision_id OR NEW.parent_run_id IS NOT OLD.parent_run_id OR NEW.created_at_utc IS NOT OLD.created_at_utc OR NOT (
 OLD.state='OPEN' AND NEW.state='MATERIALIZED' AND discovery_authorized(OLD.discovery_run_id,'MATERIALIZE')=1 OR
 OLD.state='MATERIALIZED' AND NEW.state='SEALED' AND NEW.resolution_run_id IS OLD.resolution_run_id AND NEW.materialization_sha256 IS OLD.materialization_sha256 AND NEW.materialized_at_utc IS OLD.materialized_at_utc AND discovery_authorized(OLD.discovery_run_id,'SEAL')=1)
 THEN RAISE(ABORT,'immutable discovery run transition') END;
END;
CREATE TRIGGER discovery_operation_update BEFORE UPDATE ON discovery_operation BEGIN
 SELECT CASE WHEN OLD.state='COMPLETE' OR NEW.operation_id IS NOT OLD.operation_id OR NEW.discovery_run_id IS NOT OLD.discovery_run_id OR NEW.operation_kind IS NOT OLD.operation_kind OR NEW.specification_json IS NOT OLD.specification_json OR NEW.specification_sha256 IS NOT OLD.specification_sha256 OR NEW.created_at_utc IS NOT OLD.created_at_utc OR (OLD.plan_json IS NOT NULL AND NEW.plan_json IS NOT OLD.plan_json) OR discovery_authorized(OLD.discovery_run_id,'OPEN')!=1 OR NOT EXISTS(SELECT 1 FROM discovery_run WHERE discovery_run_id=OLD.discovery_run_id AND state='OPEN') THEN RAISE(ABORT,'immutable discovery operation') END;
END;
CREATE TRIGGER discovery_event_context BEFORE INSERT ON discovery_event BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM discovery_operation o WHERE o.operation_id=NEW.operation_id AND o.discovery_run_id=NEW.discovery_run_id AND o.operation_kind=NEW.event_type AND o.state!='COMPLETE') THEN RAISE(ABORT,'discovery event operation mismatch') END;
 SELECT CASE WHEN NEW.provider_record_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM provider_record p JOIN openalex_acquisition a ON a.snapshot_id=p.snapshot_id WHERE p.provider_record_id=NEW.provider_record_id AND a.acquisition_id=NEW.acquisition_id AND a.state='COMPLETE') THEN RAISE(ABORT,'discovery requires completed work acquisition') END;
 SELECT CASE WHEN NEW.seed_record_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM entity_membership m JOIN resolution_run r ON r.resolution_run_id=m.resolution_run_id JOIN discovery_run d ON d.discovery_run_id=NEW.discovery_run_id WHERE m.resolution_run_id=NEW.seed_resolution_run_id AND m.entity_id=NEW.seed_entity_id AND m.provider_record_id=NEW.seed_record_id AND r.sealed_at_utc IS NOT NULL AND r.project_revision_id=d.project_revision_id) THEN RAISE(ABORT,'discovery seed is not resolved in this project revision') END;
END;
CREATE TRIGGER discovery_binding_context BEFORE INSERT ON discovery_operation_acquisition BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM discovery_operation o JOIN discovery_run d ON d.discovery_run_id=o.discovery_run_id JOIN openalex_acquisition a ON a.acquisition_id=NEW.acquisition_id WHERE o.operation_id=NEW.operation_id AND o.state!='COMPLETE' AND a.project_revision_id=d.project_revision_id) THEN RAISE(ABORT,'discovery acquisition revision mismatch') END;
END;

CREATE TRIGGER discovery_operation_insert BEFORE INSERT ON discovery_operation BEGIN
 SELECT CASE WHEN NEW.state!='OPEN' OR EXISTS(SELECT 1 FROM discovery_operation WHERE operation_id=NEW.operation_id OR (discovery_run_id=NEW.discovery_run_id AND specification_sha256=NEW.specification_sha256)) THEN RAISE(ABORT,'immutable discovery identity') END;
 SELECT CASE WHEN discovery_authorized(NEW.discovery_run_id,'OPEN')!=1 OR NOT EXISTS(SELECT 1 FROM discovery_run WHERE discovery_run_id=NEW.discovery_run_id AND state='OPEN') THEN RAISE(ABORT,'discovery writes locked') END;
END;
CREATE TRIGGER discovery_operation_delete BEFORE DELETE ON discovery_operation BEGIN SELECT RAISE(ABORT,'immutable discovery evidence'); END;

CREATE TRIGGER discovery_operation_acquisition_insert BEFORE INSERT ON discovery_operation_acquisition BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM discovery_operation_acquisition WHERE operation_id=NEW.operation_id AND acquisition_id=NEW.acquisition_id AND role=NEW.role) THEN RAISE(ABORT,'immutable discovery identity') END;
 SELECT CASE WHEN discovery_authorized((SELECT discovery_run_id FROM discovery_operation WHERE operation_id=NEW.operation_id),'OPEN')!=1 OR NOT EXISTS(SELECT 1 FROM discovery_run WHERE discovery_run_id=(SELECT discovery_run_id FROM discovery_operation WHERE operation_id=NEW.operation_id) AND state='OPEN') THEN RAISE(ABORT,'discovery writes locked') END;
END;
CREATE TRIGGER discovery_operation_acquisition_delete BEFORE DELETE ON discovery_operation_acquisition BEGIN SELECT RAISE(ABORT,'immutable discovery evidence'); END;
CREATE TRIGGER discovery_operation_acquisition_update BEFORE UPDATE ON discovery_operation_acquisition BEGIN SELECT RAISE(ABORT,'immutable discovery evidence'); END;

CREATE TRIGGER discovery_event_insert BEFORE INSERT ON discovery_event BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM discovery_event WHERE event_id=NEW.event_id) THEN RAISE(ABORT,'immutable discovery identity') END;
 SELECT CASE WHEN discovery_authorized(NEW.discovery_run_id,'OPEN')!=1 OR NOT EXISTS(SELECT 1 FROM discovery_run WHERE discovery_run_id=NEW.discovery_run_id AND state='OPEN') THEN RAISE(ABORT,'discovery writes locked') END;
END;
CREATE TRIGGER discovery_event_delete BEFORE DELETE ON discovery_event BEGIN SELECT RAISE(ABORT,'immutable discovery evidence'); END;
CREATE TRIGGER discovery_event_update BEFORE UPDATE ON discovery_event BEGIN SELECT RAISE(ABORT,'immutable discovery evidence'); END;

CREATE TRIGGER discovery_membership_insert BEFORE INSERT ON discovery_membership BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM discovery_membership WHERE discovery_run_id=NEW.discovery_run_id AND publication_entity_id=NEW.publication_entity_id) THEN RAISE(ABORT,'immutable discovery identity') END;
 SELECT CASE WHEN discovery_authorized(NEW.discovery_run_id,'MATERIALIZE')!=1 OR NOT EXISTS(SELECT 1 FROM discovery_run WHERE discovery_run_id=NEW.discovery_run_id AND state='OPEN') THEN RAISE(ABORT,'discovery writes locked') END;
END;
CREATE TRIGGER discovery_membership_delete BEFORE DELETE ON discovery_membership BEGIN SELECT RAISE(ABORT,'immutable discovery evidence'); END;
CREATE TRIGGER discovery_membership_update BEFORE UPDATE ON discovery_membership BEGIN SELECT RAISE(ABORT,'immutable discovery evidence'); END;

CREATE TRIGGER discovery_attempt_insert BEFORE INSERT ON discovery_attempt BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM discovery_attempt WHERE attempt_id=NEW.attempt_id) THEN RAISE(ABORT,'immutable discovery identity') END;
 SELECT CASE WHEN discovery_authorized(NEW.discovery_run_id,'OPEN')!=1 OR NOT EXISTS(SELECT 1 FROM discovery_run WHERE discovery_run_id=NEW.discovery_run_id AND state='OPEN') THEN RAISE(ABORT,'discovery writes locked') END;
END;
CREATE TRIGGER discovery_attempt_delete BEFORE DELETE ON discovery_attempt BEGIN SELECT RAISE(ABORT,'immutable discovery evidence'); END;
CREATE TRIGGER discovery_attempt_update BEFORE UPDATE ON discovery_attempt BEGIN SELECT RAISE(ABORT,'immutable discovery evidence'); END;

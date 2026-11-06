-- Stage03 conservative publication identity: immutable decisions and run projections.

CREATE TABLE resolution_run (
    resolution_run_id TEXT NOT NULL CHECK(length(trim(resolution_run_id))>0),
    project_revision_id TEXT NOT NULL REFERENCES project_revision(revision_id),
    resolver_name TEXT NOT NULL CHECK(length(trim(resolver_name))>0),
    resolver_version TEXT NOT NULL CHECK(length(trim(resolver_version))>0),
    policy_name TEXT NOT NULL CHECK(length(trim(policy_name))>0),
    policy_version TEXT NOT NULL CHECK(length(trim(policy_version))>0),
    policy_json TEXT NOT NULL CHECK(CASE WHEN json_valid(policy_json) THEN json_type(policy_json)='object' ELSE 0 END),
    policy_sha256 TEXT NOT NULL CHECK(policy_sha256 IS NULL OR (length(policy_sha256)=64 AND policy_sha256 NOT GLOB '*[^0-9a-f]*')),
    observation_selector_json TEXT NOT NULL CHECK(CASE WHEN json_valid(observation_selector_json) THEN json_type(observation_selector_json)='object' ELSE 0 END),
    observation_selector_sha256 TEXT NOT NULL CHECK(observation_selector_sha256 IS NULL OR (length(observation_selector_sha256)=64 AND observation_selector_sha256 NOT GLOB '*[^0-9a-f]*')),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc IS NULL OR (created_at_utc GLOB '*Z' AND julianday(created_at_utc) IS NOT NULL)),
    candidates_generated_at_utc TEXT CHECK(candidates_generated_at_utc IS NULL OR (candidates_generated_at_utc GLOB '*Z' AND julianday(candidates_generated_at_utc) IS NOT NULL)),
    candidate_manifest_sha256 TEXT CHECK(candidate_manifest_sha256 IS NULL OR (length(candidate_manifest_sha256)=64 AND candidate_manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    materialized_at_utc TEXT CHECK(materialized_at_utc IS NULL OR (materialized_at_utc GLOB '*Z' AND julianday(materialized_at_utc) IS NOT NULL)),
    sealed_at_utc TEXT CHECK(sealed_at_utc IS NULL OR (sealed_at_utc GLOB '*Z' AND julianday(sealed_at_utc) IS NOT NULL)),
    input_manifest_sha256 TEXT CHECK(input_manifest_sha256 IS NULL OR (length(input_manifest_sha256)=64 AND input_manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    materialization_manifest_sha256 TEXT CHECK(materialization_manifest_sha256 IS NULL OR (length(materialization_manifest_sha256)=64 AND materialization_manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    decision_manifest_json TEXT CHECK(decision_manifest_json IS NULL OR (json_valid(decision_manifest_json) AND json_type(decision_manifest_json)='array')),
    relation_manifest_sha256 TEXT CHECK(relation_manifest_sha256 IS NULL OR (length(relation_manifest_sha256)=64 AND relation_manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    PRIMARY KEY(resolution_run_id),
    CHECK(policy_json=resolution_canonical_json(policy_json) AND policy_sha256=foundation_sha256_text(policy_json)),
    CHECK(observation_selector_json=resolution_canonical_json(observation_selector_json) AND observation_selector_sha256=foundation_sha256_text(observation_selector_json)),
    CHECK(sealed_at_utc IS NULL OR materialized_at_utc IS NOT NULL),
    CHECK((candidates_generated_at_utc IS NULL)=(candidate_manifest_sha256 IS NULL)),
    CHECK((materialized_at_utc IS NULL)=(input_manifest_sha256 IS NULL) AND (materialized_at_utc IS NULL)=(materialization_manifest_sha256 IS NULL) AND (materialized_at_utc IS NULL)=(decision_manifest_json IS NULL)),
    CHECK((sealed_at_utc IS NULL)=(relation_manifest_sha256 IS NULL))
);

CREATE TABLE resolution_run_input (
    resolution_run_id TEXT NOT NULL REFERENCES resolution_run(resolution_run_id),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    evidence_json TEXT NOT NULL CHECK(CASE WHEN json_valid(evidence_json) THEN json_type(evidence_json)='object' ELSE 0 END),
    added_at_utc TEXT NOT NULL CHECK(added_at_utc IS NULL OR (added_at_utc GLOB '*Z' AND julianday(added_at_utc) IS NOT NULL)),
    PRIMARY KEY(resolution_run_id,provider_record_id)
);

CREATE TABLE resolution_candidate (
    candidate_id TEXT NOT NULL CHECK(length(trim(candidate_id))>0),
    resolution_run_id TEXT NOT NULL REFERENCES resolution_run(resolution_run_id),
    left_provider_record_id TEXT NOT NULL,
    right_provider_record_id TEXT NOT NULL,
    candidate_methods_json TEXT NOT NULL CHECK(CASE WHEN json_valid(candidate_methods_json) THEN json_type(candidate_methods_json)='array' ELSE 0 END),
    evidence_json TEXT NOT NULL CHECK(CASE WHEN json_valid(evidence_json) THEN json_type(evidence_json)='object' ELSE 0 END),
    hard_conflicts_json TEXT NOT NULL CHECK(CASE WHEN json_valid(hard_conflicts_json) THEN json_type(hard_conflicts_json)='array' ELSE 0 END),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc IS NULL OR (created_at_utc GLOB '*Z' AND julianday(created_at_utc) IS NOT NULL)),
    PRIMARY KEY(candidate_id),
    UNIQUE(resolution_run_id,candidate_id),
    UNIQUE(resolution_run_id,left_provider_record_id,right_provider_record_id),
    CHECK(left_provider_record_id < right_provider_record_id),
    CHECK(candidate_methods_json=resolution_sorted_array(candidate_methods_json)),
    FOREIGN KEY(resolution_run_id,left_provider_record_id) REFERENCES resolution_run_input(resolution_run_id,provider_record_id),
    FOREIGN KEY(resolution_run_id,right_provider_record_id) REFERENCES resolution_run_input(resolution_run_id,provider_record_id)
);

CREATE TABLE entity_resolution_decision (
    decision_id TEXT NOT NULL CHECK(length(trim(decision_id))>0),
    resolution_run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    decision_type TEXT NOT NULL CHECK(decision_type IN ('SAME','DISTINCT','AMBIGUOUS')),
    decision_mode TEXT NOT NULL CHECK(decision_mode IN ('AUTOMATIC','MANUAL','IMPORTED')),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code))>0),
    evidence_json TEXT NOT NULL CHECK(CASE WHEN json_valid(evidence_json) THEN json_type(evidence_json)='object' ELSE 0 END),
    hard_conflicts_json TEXT NOT NULL CHECK(CASE WHEN json_valid(hard_conflicts_json) THEN json_type(hard_conflicts_json)='array' ELSE 0 END),
    actor_id TEXT,
    note TEXT,
    supersedes_decision_id TEXT REFERENCES entity_resolution_decision(decision_id),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc IS NULL OR (created_at_utc GLOB '*Z' AND julianday(created_at_utc) IS NOT NULL)),
    PRIMARY KEY(decision_id),
    UNIQUE(supersedes_decision_id),
    FOREIGN KEY(resolution_run_id,candidate_id) REFERENCES resolution_candidate(resolution_run_id,candidate_id),
    CHECK(decision_mode!='MANUAL' OR length(trim(actor_id))>0 AND actor_id IS NOT NULL),
    CHECK(decision_mode!='AUTOMATIC' OR decision_type!='DISTINCT'),
    CHECK(supersedes_decision_id IS NULL OR supersedes_decision_id!=decision_id)
);

CREATE TABLE publication_entity (
    resolution_run_id TEXT NOT NULL REFERENCES resolution_run(resolution_run_id),
    entity_id TEXT NOT NULL CHECK(length(trim(entity_id))>0),
    membership_sha256 TEXT NOT NULL CHECK(membership_sha256 IS NULL OR (length(membership_sha256)=64 AND membership_sha256 NOT GLOB '*[^0-9a-f]*')),
    member_count INTEGER NOT NULL CHECK(typeof(member_count)='integer' AND member_count>=1),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc IS NULL OR (created_at_utc GLOB '*Z' AND julianday(created_at_utc) IS NOT NULL)),
    PRIMARY KEY(resolution_run_id,entity_id)
);

CREATE TABLE entity_membership (
    resolution_run_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    provider_record_id TEXT NOT NULL,
    membership_basis_json TEXT NOT NULL CHECK(CASE WHEN json_valid(membership_basis_json) THEN json_type(membership_basis_json)='object' ELSE 0 END),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc IS NULL OR (created_at_utc GLOB '*Z' AND julianday(created_at_utc) IS NOT NULL)),
    PRIMARY KEY(resolution_run_id,entity_id,provider_record_id),
    CONSTRAINT one_entity_per_input UNIQUE(resolution_run_id,provider_record_id),
    FOREIGN KEY(resolution_run_id,entity_id) REFERENCES publication_entity(resolution_run_id,entity_id),
    FOREIGN KEY(resolution_run_id,provider_record_id) REFERENCES resolution_run_input(resolution_run_id,provider_record_id)
);

CREATE TABLE publication_relation (
    relation_id TEXT NOT NULL CHECK(length(trim(relation_id))>0),
    resolution_run_id TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK(relation_type IN ('PREPRINT_OF','ACCEPTED_MANUSCRIPT_OF','REPOSITORY_COPY_OF','CONFERENCE_PRECURSOR_OF','EXTENDED_VERSION_OF','CORRECTION_OF','RETRACTION_OF','POSSIBLY_RELATED_VERSION')),
    decision_mode TEXT NOT NULL CHECK(decision_mode IN ('MANUAL','IMPORTED')),
    evidence_json TEXT NOT NULL CHECK(CASE WHEN json_valid(evidence_json) THEN json_type(evidence_json)='object' ELSE 0 END),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code))>0),
    actor_id TEXT,
    note TEXT,
    supersedes_relation_id TEXT REFERENCES publication_relation(relation_id),
    created_at_utc TEXT NOT NULL CHECK(created_at_utc IS NULL OR (created_at_utc GLOB '*Z' AND julianday(created_at_utc) IS NOT NULL)),
    PRIMARY KEY(relation_id),
    UNIQUE(supersedes_relation_id),
    FOREIGN KEY(resolution_run_id,source_entity_id) REFERENCES publication_entity(resolution_run_id,entity_id),
    FOREIGN KEY(resolution_run_id,target_entity_id) REFERENCES publication_entity(resolution_run_id,entity_id),
    CHECK(source_entity_id!=target_entity_id),
    CHECK(decision_mode!='MANUAL' OR actor_id IS NOT NULL AND length(trim(actor_id))>0),
    CHECK(supersedes_relation_id IS NULL OR supersedes_relation_id!=relation_id)
);

CREATE TRIGGER resolution_run_no_replace BEFORE INSERT ON resolution_run
WHEN EXISTS(SELECT 1 FROM resolution_run WHERE resolution_run_id=NEW.resolution_run_id)
BEGIN SELECT RAISE(ABORT,'immutable resolution identity'); END;

CREATE TRIGGER resolution_run_no_delete BEFORE DELETE ON resolution_run
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER resolution_run_input_no_replace BEFORE INSERT ON resolution_run_input
WHEN EXISTS(SELECT 1 FROM resolution_run_input WHERE resolution_run_id=NEW.resolution_run_id AND provider_record_id=NEW.provider_record_id)
BEGIN SELECT RAISE(ABORT,'immutable resolution identity'); END;

CREATE TRIGGER resolution_run_input_no_delete BEFORE DELETE ON resolution_run_input
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER resolution_run_input_no_update BEFORE UPDATE ON resolution_run_input
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER resolution_candidate_no_replace BEFORE INSERT ON resolution_candidate
WHEN EXISTS(SELECT 1 FROM resolution_candidate WHERE candidate_id=NEW.candidate_id OR (resolution_run_id=NEW.resolution_run_id AND left_provider_record_id=NEW.left_provider_record_id AND right_provider_record_id=NEW.right_provider_record_id))
BEGIN SELECT RAISE(ABORT,'immutable resolution identity'); END;

CREATE TRIGGER resolution_candidate_no_delete BEFORE DELETE ON resolution_candidate
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER resolution_candidate_no_update BEFORE UPDATE ON resolution_candidate
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER entity_resolution_decision_no_replace BEFORE INSERT ON entity_resolution_decision
WHEN EXISTS(SELECT 1 FROM entity_resolution_decision WHERE decision_id=NEW.decision_id OR (NEW.supersedes_decision_id IS NOT NULL AND supersedes_decision_id=NEW.supersedes_decision_id))
BEGIN SELECT RAISE(ABORT,'immutable resolution identity'); END;

CREATE TRIGGER entity_resolution_decision_no_delete BEFORE DELETE ON entity_resolution_decision
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER entity_resolution_decision_no_update BEFORE UPDATE ON entity_resolution_decision
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER publication_entity_no_replace BEFORE INSERT ON publication_entity
WHEN EXISTS(SELECT 1 FROM publication_entity WHERE resolution_run_id=NEW.resolution_run_id AND entity_id=NEW.entity_id)
BEGIN SELECT RAISE(ABORT,'immutable resolution identity'); END;

CREATE TRIGGER publication_entity_no_delete BEFORE DELETE ON publication_entity
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER publication_entity_no_update BEFORE UPDATE ON publication_entity
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER entity_membership_no_replace BEFORE INSERT ON entity_membership
WHEN EXISTS(SELECT 1 FROM entity_membership WHERE resolution_run_id=NEW.resolution_run_id AND provider_record_id=NEW.provider_record_id)
BEGIN SELECT RAISE(ABORT,'immutable resolution identity'); END;

CREATE TRIGGER entity_membership_no_delete BEFORE DELETE ON entity_membership
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER entity_membership_no_update BEFORE UPDATE ON entity_membership
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER publication_relation_no_replace BEFORE INSERT ON publication_relation
WHEN EXISTS(SELECT 1 FROM publication_relation WHERE relation_id=NEW.relation_id OR (NEW.supersedes_relation_id IS NOT NULL AND supersedes_relation_id=NEW.supersedes_relation_id))
BEGIN SELECT RAISE(ABORT,'immutable resolution identity'); END;

CREATE TRIGGER publication_relation_no_delete BEFORE DELETE ON publication_relation
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER publication_relation_no_update BEFORE UPDATE ON publication_relation
BEGIN SELECT RAISE(ABORT,'immutable resolution object'); END;

CREATE TRIGGER resolution_run_start_open BEFORE INSERT ON resolution_run
BEGIN SELECT CASE WHEN NEW.materialized_at_utc IS NOT NULL OR NEW.sealed_at_utc IS NOT NULL OR NEW.candidates_generated_at_utc IS NOT NULL OR NEW.input_manifest_sha256 IS NOT NULL OR NEW.materialization_manifest_sha256 IS NOT NULL THEN RAISE(ABORT,'run must start open') END; END;

CREATE TRIGGER resolution_input_open BEFORE INSERT ON resolution_run_input
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM resolution_run WHERE resolution_run_id=NEW.resolution_run_id AND materialized_at_utc IS NULL AND candidates_generated_at_utc IS NULL) OR resolution_authorized(NEW.resolution_run_id,'INPUT')!=1 THEN RAISE(ABORT,'resolution inputs locked') END; END;

CREATE TRIGGER resolution_input_sealed BEFORE INSERT ON resolution_run_input
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM provider_record r JOIN provider_snapshot s ON s.snapshot_id=r.snapshot_id WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL) THEN RAISE(ABORT,'resolution requires sealed provider record') END; END;

CREATE TRIGGER resolution_candidate_open BEFORE INSERT ON resolution_candidate
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM resolution_run WHERE resolution_run_id=NEW.resolution_run_id AND materialized_at_utc IS NULL AND candidates_generated_at_utc IS NULL) OR resolution_authorized(NEW.resolution_run_id,'GENERATE')!=1 THEN RAISE(ABORT,'candidate generation locked') END; END;

CREATE TRIGGER decision_run_open BEFORE INSERT ON entity_resolution_decision
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM resolution_run WHERE resolution_run_id=NEW.resolution_run_id AND materialized_at_utc IS NULL AND candidates_generated_at_utc IS NOT NULL) THEN RAISE(ABORT,'resolution decisions locked') END; END;

CREATE TRIGGER publication_entity_materializing BEFORE INSERT ON publication_entity
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM resolution_run WHERE resolution_run_id=NEW.resolution_run_id AND materialized_at_utc IS NULL AND candidates_generated_at_utc IS NOT NULL) OR resolution_authorized(NEW.resolution_run_id,'MATERIALIZE')!=1 THEN RAISE(ABORT,'entity materialization locked') END; END;

CREATE TRIGGER entity_membership_materializing BEFORE INSERT ON entity_membership
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM resolution_run WHERE resolution_run_id=NEW.resolution_run_id AND materialized_at_utc IS NULL AND candidates_generated_at_utc IS NOT NULL) OR resolution_authorized(NEW.resolution_run_id,'MATERIALIZE')!=1 THEN RAISE(ABORT,'entity materialization locked') END; END;

CREATE TRIGGER relation_materialized BEFORE INSERT ON publication_relation
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM resolution_run WHERE resolution_run_id=NEW.resolution_run_id AND materialized_at_utc IS NOT NULL AND sealed_at_utc IS NULL) THEN RAISE(ABORT,'publication relations locked') END; END;

CREATE TRIGGER decision_supersession BEFORE INSERT ON entity_resolution_decision
BEGIN
    SELECT CASE WHEN EXISTS(SELECT 1 FROM active_entity_resolution_decision WHERE candidate_id=NEW.candidate_id
        AND (NEW.supersedes_decision_id IS NULL OR decision_id!=NEW.supersedes_decision_id))
        THEN RAISE(ABORT,'explicit active decision supersession required') END;
    SELECT CASE WHEN NEW.supersedes_decision_id IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM entity_resolution_decision old
        JOIN resolution_candidate oc ON oc.candidate_id=old.candidate_id
        JOIN resolution_candidate nc ON nc.candidate_id=NEW.candidate_id
        JOIN resolution_run rr ON rr.resolution_run_id=old.resolution_run_id
        WHERE old.decision_id=NEW.supersedes_decision_id
        AND oc.left_provider_record_id=nc.left_provider_record_id AND oc.right_provider_record_id=nc.right_provider_record_id
        AND (old.candidate_id=NEW.candidate_id OR (old.resolution_run_id!=NEW.resolution_run_id AND rr.sealed_at_utc IS NOT NULL))
        AND NOT EXISTS(SELECT 1 FROM entity_resolution_decision child WHERE child.supersedes_decision_id=old.decision_id))
        THEN RAISE(ABORT,'invalid decision supersession') END;
END;

CREATE TRIGGER relation_supersession BEFORE INSERT ON publication_relation
WHEN NEW.supersedes_relation_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM publication_relation old WHERE old.relation_id=NEW.supersedes_relation_id
        AND old.resolution_run_id=NEW.resolution_run_id AND old.source_entity_id=NEW.source_entity_id
        AND old.target_entity_id=NEW.target_entity_id AND NOT EXISTS(SELECT 1 FROM publication_relation child WHERE child.supersedes_relation_id=old.relation_id))
        THEN RAISE(ABORT,'invalid relation supersession') END;
END;

CREATE TRIGGER resolution_run_transition BEFORE UPDATE ON resolution_run
BEGIN
    SELECT CASE WHEN OLD.sealed_at_utc IS NOT NULL OR NEW.resolution_run_id IS NOT OLD.resolution_run_id OR NEW.project_revision_id IS NOT OLD.project_revision_id OR NEW.resolver_name IS NOT OLD.resolver_name OR NEW.resolver_version IS NOT OLD.resolver_version OR NEW.policy_name IS NOT OLD.policy_name OR NEW.policy_version IS NOT OLD.policy_version OR NEW.policy_json IS NOT OLD.policy_json OR NEW.policy_sha256 IS NOT OLD.policy_sha256 OR NEW.observation_selector_json IS NOT OLD.observation_selector_json OR NEW.observation_selector_sha256 IS NOT OLD.observation_selector_sha256 OR NEW.created_at_utc IS NOT OLD.created_at_utc THEN RAISE(ABORT,'immutable resolution run') END;
    SELECT CASE WHEN NOT (
        (resolution_authorized(OLD.resolution_run_id,'GENERATE')=1 AND OLD.candidates_generated_at_utc IS NULL
         AND NEW.candidates_generated_at_utc IS NOT NULL AND NEW.candidate_manifest_sha256 IS NOT NULL
         AND NEW.materialized_at_utc IS OLD.materialized_at_utc AND NEW.input_manifest_sha256 IS OLD.input_manifest_sha256 AND NEW.materialization_manifest_sha256 IS OLD.materialization_manifest_sha256 AND NEW.decision_manifest_json IS OLD.decision_manifest_json AND NEW.sealed_at_utc IS OLD.sealed_at_utc AND NEW.relation_manifest_sha256 IS OLD.relation_manifest_sha256)
        OR (resolution_authorized(OLD.resolution_run_id,'MATERIALIZE')=1 AND OLD.materialized_at_utc IS NULL
         AND OLD.candidates_generated_at_utc IS NOT NULL AND NEW.materialized_at_utc IS NOT NULL
         AND NEW.candidates_generated_at_utc IS OLD.candidates_generated_at_utc AND NEW.candidate_manifest_sha256 IS OLD.candidate_manifest_sha256 AND NEW.sealed_at_utc IS OLD.sealed_at_utc AND NEW.relation_manifest_sha256 IS OLD.relation_manifest_sha256)
        OR (resolution_authorized(OLD.resolution_run_id,'SEAL')=1 AND OLD.materialized_at_utc IS NOT NULL
         AND NEW.sealed_at_utc IS NOT NULL AND NEW.candidates_generated_at_utc IS OLD.candidates_generated_at_utc AND NEW.candidate_manifest_sha256 IS OLD.candidate_manifest_sha256 AND NEW.materialized_at_utc IS OLD.materialized_at_utc AND NEW.input_manifest_sha256 IS OLD.input_manifest_sha256 AND NEW.materialization_manifest_sha256 IS OLD.materialization_manifest_sha256 AND NEW.decision_manifest_json IS OLD.decision_manifest_json))
        THEN RAISE(ABORT,'unauthorized resolution lifecycle transition') END;
END;

CREATE VIEW active_entity_resolution_decision AS
SELECT d.* FROM entity_resolution_decision d WHERE NOT EXISTS(
    SELECT 1 FROM entity_resolution_decision child WHERE child.supersedes_decision_id=d.decision_id);

CREATE VIEW resolution_review_queue AS
SELECT c.*,d.decision_id,d.decision_type,d.decision_mode,d.reason_code,r.materialized_at_utc,r.sealed_at_utc,
CASE WHEN d.decision_id IS NULL THEN 'UNRESOLVED' WHEN d.decision_type='AMBIGUOUS' THEN 'NEEDS_REVIEW'
WHEN d.decision_mode='AUTOMATIC' AND d.decision_type='SAME' THEN 'AUTO_MATCHED'
WHEN d.decision_mode='AUTOMATIC' THEN 'AUTO_DISTINCT'
WHEN d.decision_type='SAME' THEN 'MANUAL_MATCHED' ELSE 'MANUAL_DISTINCT' END AS review_status
FROM resolution_candidate c JOIN resolution_run r ON r.resolution_run_id=c.resolution_run_id
LEFT JOIN active_entity_resolution_decision d ON d.candidate_id=c.candidate_id;

CREATE VIEW active_publication_relation AS
SELECT r.* FROM publication_relation r WHERE NOT EXISTS(SELECT 1 FROM publication_relation child WHERE child.supersedes_relation_id=r.relation_id);

CREATE INDEX decision_candidate ON entity_resolution_decision(candidate_id);

CREATE INDEX relation_pair ON publication_relation(resolution_run_id,source_entity_id,target_entity_id);

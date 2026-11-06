CREATE TABLE screening_run (
 screening_run_id TEXT PRIMARY KEY NOT NULL, project_revision_id TEXT NOT NULL REFERENCES project_revision(revision_id),
 discovery_run_id TEXT NOT NULL REFERENCES discovery_run(discovery_run_id), discovery_sha256 TEXT NOT NULL,
 frontier_run_id TEXT REFERENCES frontier_run(frontier_run_id), frontier_sha256 TEXT,
 plan_json TEXT NOT NULL CHECK(json_valid(plan_json)), plan_sha256 TEXT NOT NULL CHECK(plan_sha256=foundation_sha256_text(plan_json)),
 candidate_set_sha256 TEXT NOT NULL, assignment_sha256 TEXT NOT NULL,
 unresolved_discovery_json TEXT NOT NULL CHECK(json_valid(unresolved_discovery_json)), unresolved_discovery_sha256 TEXT NOT NULL CHECK(unresolved_discovery_sha256=foundation_sha256_text(unresolved_discovery_json)),
 state TEXT NOT NULL CHECK(state IN ('OPEN','REVIEW_COMPLETE','RESOLVED','SEALED')),
 current_resolution_id TEXT REFERENCES screening_resolution(resolution_id), finalization_sha256 TEXT,
 created_at_utc TEXT NOT NULL, sealed_at_utc TEXT,
 CHECK((state='SEALED')=(finalization_sha256 IS NOT NULL)), CHECK((state='SEALED')=(sealed_at_utc IS NOT NULL)), CHECK((frontier_run_id IS NULL)=(frontier_sha256 IS NULL))
);
CREATE TABLE screening_reviewer (
 screening_run_id TEXT NOT NULL REFERENCES screening_run(screening_run_id), reviewer_label TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('REVIEWER','ADJUDICATOR')), actor_type TEXT NOT NULL CHECK(actor_type='HUMAN'),
 PRIMARY KEY(screening_run_id,reviewer_label)
);
CREATE TABLE screening_candidate (
 candidate_id TEXT PRIMARY KEY NOT NULL, screening_run_id TEXT NOT NULL REFERENCES screening_run(screening_run_id),
 publication_entity_id TEXT NOT NULL, discovery_resolution_run_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal>=0), membership_basis_json TEXT NOT NULL CHECK(json_valid(membership_basis_json)),
 evidence_refs_json TEXT NOT NULL CHECK(json_valid(evidence_refs_json)), evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)), evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256=foundation_sha256_text(evidence_json)),
 UNIQUE(screening_run_id,candidate_id), UNIQUE(screening_run_id,publication_entity_id), UNIQUE(screening_run_id,ordinal),
 FOREIGN KEY(discovery_resolution_run_id,publication_entity_id) REFERENCES publication_entity(resolution_run_id,entity_id)
);
CREATE TABLE screening_assignment (
 screening_run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, reviewer_label TEXT NOT NULL,
 PRIMARY KEY(screening_run_id,candidate_id,reviewer_label),
 FOREIGN KEY(screening_run_id,candidate_id) REFERENCES screening_candidate(screening_run_id,candidate_id),
 FOREIGN KEY(screening_run_id,reviewer_label) REFERENCES screening_reviewer(screening_run_id,reviewer_label)
);
CREATE TABLE screening_decision (
 decision_id TEXT PRIMARY KEY NOT NULL, screening_run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, reviewer_label TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('SCREEN_INCLUDE','SCREEN_EXCLUDE','SCREEN_UNSURE')),
 reason_codes_json TEXT NOT NULL CHECK(json_valid(reason_codes_json) AND json_type(reason_codes_json)='array'), note TEXT,
 supersedes_decision_id TEXT UNIQUE REFERENCES screening_decision(decision_id), created_at_utc TEXT NOT NULL,
 FOREIGN KEY(screening_run_id,candidate_id,reviewer_label) REFERENCES screening_assignment(screening_run_id,candidate_id,reviewer_label)
);
CREATE UNIQUE INDEX screening_decision_root ON screening_decision(screening_run_id,candidate_id,reviewer_label) WHERE supersedes_decision_id IS NULL;
CREATE TABLE screening_adjudication (
 adjudication_id TEXT PRIMARY KEY NOT NULL, screening_run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, adjudicator_label TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('SCREEN_INCLUDE','SCREEN_EXCLUDE')),
 reason_codes_json TEXT NOT NULL CHECK(json_valid(reason_codes_json) AND json_type(reason_codes_json)='array'), note TEXT,
 input_decision_ids_json TEXT NOT NULL CHECK(json_valid(input_decision_ids_json) AND json_type(input_decision_ids_json)='array'),
 supersedes_adjudication_id TEXT UNIQUE REFERENCES screening_adjudication(adjudication_id), created_at_utc TEXT NOT NULL,
 FOREIGN KEY(screening_run_id,candidate_id) REFERENCES screening_candidate(screening_run_id,candidate_id),
 FOREIGN KEY(screening_run_id,adjudicator_label) REFERENCES screening_reviewer(screening_run_id,reviewer_label)
);
CREATE UNIQUE INDEX screening_adjudication_root ON screening_adjudication(screening_run_id,candidate_id) WHERE supersedes_adjudication_id IS NULL;
CREATE TABLE screening_resolution (
 resolution_id TEXT PRIMARY KEY NOT NULL, screening_run_id TEXT NOT NULL REFERENCES screening_run(screening_run_id),
 input_sha256 TEXT NOT NULL, outcome_set_sha256 TEXT NOT NULL, created_at_utc TEXT NOT NULL,
 UNIQUE(screening_run_id,input_sha256), UNIQUE(resolution_id,screening_run_id)
);
CREATE TABLE screening_outcome (
 resolution_id TEXT NOT NULL, screening_run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, publication_entity_id TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('SCREEN_INCLUDE','SCREEN_EXCLUDE','UNRESOLVED')),
 status TEXT NOT NULL CHECK(status IN ('FINAL','PENDING','CONFLICT','UNSURE')),
 resolution_basis TEXT NOT NULL CHECK(resolution_basis IN ('AUTO_CONSENSUS','ADJUDICATED','PENDING')),
 decision_ids_json TEXT NOT NULL CHECK(json_valid(decision_ids_json)), adjudication_id TEXT REFERENCES screening_adjudication(adjudication_id),
 reason_codes_json TEXT NOT NULL CHECK(json_valid(reason_codes_json)), outcome_sha256 TEXT NOT NULL,
 PRIMARY KEY(resolution_id,candidate_id),
 FOREIGN KEY(resolution_id,screening_run_id) REFERENCES screening_resolution(resolution_id,screening_run_id),
 FOREIGN KEY(screening_run_id,candidate_id) REFERENCES screening_candidate(screening_run_id,candidate_id),
 CHECK((outcome='UNRESOLVED')=(status!='FINAL')), CHECK((resolution_basis='ADJUDICATED')=(adjudication_id IS NOT NULL))
);

CREATE TRIGGER screening_run_insert BEFORE INSERT ON screening_run BEGIN
 SELECT CASE WHEN screening_authorized(NEW.screening_run_id,'CREATE')!=1 OR EXISTS(SELECT 1 FROM screening_run WHERE screening_run_id=NEW.screening_run_id) THEN RAISE(ABORT,'immutable screening identity') END;
 SELECT CASE WHEN NEW.state!='OPEN' OR NEW.current_resolution_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM discovery_run d WHERE d.discovery_run_id=NEW.discovery_run_id AND d.state='SEALED' AND d.project_revision_id=NEW.project_revision_id AND d.materialization_sha256=NEW.discovery_sha256) OR EXISTS(SELECT 1 FROM frontier_run f WHERE f.discovery_run_id=NEW.discovery_run_id AND (f.state!='SEALED' OR f.frontier_run_id IS NOT NEW.frontier_run_id OR f.finalization_sha256 IS NOT NEW.frontier_sha256)) THEN RAISE(ABORT,'screening requires sealed input') END;
END;
CREATE TRIGGER screening_run_delete BEFORE DELETE ON screening_run BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;

CREATE TRIGGER screening_reviewer_insert BEFORE INSERT ON screening_reviewer BEGIN
 SELECT CASE WHEN screening_authorized(NEW.screening_run_id,'CREATE')!=1 OR EXISTS(SELECT 1 FROM screening_reviewer WHERE screening_run_id=NEW.screening_run_id AND reviewer_label=NEW.reviewer_label) THEN RAISE(ABORT,'immutable screening identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_run WHERE screening_run_id=NEW.screening_run_id AND state!='SEALED') THEN RAISE(ABORT,'screening sealed') END;
END;
CREATE TRIGGER screening_reviewer_delete BEFORE DELETE ON screening_reviewer BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;
CREATE TRIGGER screening_reviewer_update BEFORE UPDATE ON screening_reviewer BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;

CREATE TRIGGER screening_candidate_insert BEFORE INSERT ON screening_candidate BEGIN
 SELECT CASE WHEN screening_authorized(NEW.screening_run_id,'CREATE')!=1 OR EXISTS(SELECT 1 FROM screening_candidate WHERE candidate_id=NEW.candidate_id OR (screening_run_id=NEW.screening_run_id AND (publication_entity_id=NEW.publication_entity_id OR ordinal=NEW.ordinal))) THEN RAISE(ABORT,'immutable screening identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_run WHERE screening_run_id=NEW.screening_run_id AND state!='SEALED') THEN RAISE(ABORT,'screening sealed') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_run r JOIN discovery_membership m ON m.discovery_run_id=r.discovery_run_id WHERE r.screening_run_id=NEW.screening_run_id AND m.publication_entity_id=NEW.publication_entity_id AND m.resolution_run_id=NEW.discovery_resolution_run_id AND m.membership_basis_json=NEW.membership_basis_json) THEN RAISE(ABORT,'candidate not in final discovery') END;
END;
CREATE TRIGGER screening_candidate_delete BEFORE DELETE ON screening_candidate BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;
CREATE TRIGGER screening_candidate_update BEFORE UPDATE ON screening_candidate BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;

CREATE TRIGGER screening_assignment_insert BEFORE INSERT ON screening_assignment BEGIN
 SELECT CASE WHEN screening_authorized(NEW.screening_run_id,'CREATE')!=1 OR EXISTS(SELECT 1 FROM screening_assignment WHERE screening_run_id=NEW.screening_run_id AND candidate_id=NEW.candidate_id AND reviewer_label=NEW.reviewer_label) THEN RAISE(ABORT,'immutable screening identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_run WHERE screening_run_id=NEW.screening_run_id AND state!='SEALED') THEN RAISE(ABORT,'screening sealed') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_reviewer WHERE screening_run_id=NEW.screening_run_id AND reviewer_label=NEW.reviewer_label AND role='REVIEWER') THEN RAISE(ABORT,'invalid assigned reviewer') END;
END;
CREATE TRIGGER screening_assignment_delete BEFORE DELETE ON screening_assignment BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;
CREATE TRIGGER screening_assignment_update BEFORE UPDATE ON screening_assignment BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;

CREATE TRIGGER screening_decision_insert BEFORE INSERT ON screening_decision BEGIN
 SELECT CASE WHEN screening_authorized(NEW.screening_run_id,'WRITE')!=1 OR EXISTS(SELECT 1 FROM screening_decision WHERE decision_id=NEW.decision_id OR (NEW.supersedes_decision_id IS NOT NULL AND supersedes_decision_id=NEW.supersedes_decision_id) OR (NEW.supersedes_decision_id IS NULL AND supersedes_decision_id IS NULL AND screening_run_id=NEW.screening_run_id AND candidate_id=NEW.candidate_id AND reviewer_label=NEW.reviewer_label)) THEN RAISE(ABORT,'immutable screening identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_run WHERE screening_run_id=NEW.screening_run_id AND state!='SEALED') THEN RAISE(ABORT,'screening sealed') END;
 SELECT CASE WHEN NEW.supersedes_decision_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM screening_decision WHERE decision_id=NEW.supersedes_decision_id AND screening_run_id=NEW.screening_run_id AND candidate_id=NEW.candidate_id AND reviewer_label=NEW.reviewer_label) OR EXISTS(SELECT 1 FROM screening_decision WHERE supersedes_decision_id=NEW.supersedes_decision_id)) THEN RAISE(ABORT,'invalid screening supersession') END;
END;
CREATE TRIGGER screening_decision_delete BEFORE DELETE ON screening_decision BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;
CREATE TRIGGER screening_decision_update BEFORE UPDATE ON screening_decision BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;

CREATE TRIGGER screening_adjudication_insert BEFORE INSERT ON screening_adjudication BEGIN
 SELECT CASE WHEN screening_authorized(NEW.screening_run_id,'WRITE')!=1 OR EXISTS(SELECT 1 FROM screening_adjudication WHERE adjudication_id=NEW.adjudication_id OR (NEW.supersedes_adjudication_id IS NOT NULL AND supersedes_adjudication_id=NEW.supersedes_adjudication_id) OR (NEW.supersedes_adjudication_id IS NULL AND supersedes_adjudication_id IS NULL AND screening_run_id=NEW.screening_run_id AND candidate_id=NEW.candidate_id)) THEN RAISE(ABORT,'immutable screening identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_run WHERE screening_run_id=NEW.screening_run_id AND state!='SEALED') THEN RAISE(ABORT,'screening sealed') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_reviewer WHERE screening_run_id=NEW.screening_run_id AND reviewer_label=NEW.adjudicator_label AND role='ADJUDICATOR') OR (NEW.supersedes_adjudication_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM screening_adjudication WHERE adjudication_id=NEW.supersedes_adjudication_id AND screening_run_id=NEW.screening_run_id AND candidate_id=NEW.candidate_id) OR EXISTS(SELECT 1 FROM screening_adjudication WHERE supersedes_adjudication_id=NEW.supersedes_adjudication_id))) THEN RAISE(ABORT,'invalid human adjudication context') END;
END;
CREATE TRIGGER screening_adjudication_delete BEFORE DELETE ON screening_adjudication BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;
CREATE TRIGGER screening_adjudication_update BEFORE UPDATE ON screening_adjudication BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;

CREATE TRIGGER screening_resolution_insert BEFORE INSERT ON screening_resolution BEGIN
 SELECT CASE WHEN screening_authorized(NEW.screening_run_id,'WRITE')!=1 OR EXISTS(SELECT 1 FROM screening_resolution WHERE resolution_id=NEW.resolution_id OR (screening_run_id=NEW.screening_run_id AND input_sha256=NEW.input_sha256)) THEN RAISE(ABORT,'immutable screening identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_run WHERE screening_run_id=NEW.screening_run_id AND state!='SEALED') THEN RAISE(ABORT,'screening sealed') END;
END;
CREATE TRIGGER screening_resolution_delete BEFORE DELETE ON screening_resolution BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;
CREATE TRIGGER screening_resolution_update BEFORE UPDATE ON screening_resolution BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;

CREATE TRIGGER screening_outcome_insert BEFORE INSERT ON screening_outcome BEGIN
 SELECT CASE WHEN screening_authorized(NEW.screening_run_id,'WRITE')!=1 OR EXISTS(SELECT 1 FROM screening_outcome WHERE resolution_id=NEW.resolution_id AND candidate_id=NEW.candidate_id) THEN RAISE(ABORT,'immutable screening identity') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM screening_run WHERE screening_run_id=NEW.screening_run_id AND state!='SEALED') THEN RAISE(ABORT,'screening sealed') END;
END;
CREATE TRIGGER screening_outcome_delete BEFORE DELETE ON screening_outcome BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;
CREATE TRIGGER screening_outcome_update BEFORE UPDATE ON screening_outcome BEGIN SELECT RAISE(ABORT,'immutable screening evidence'); END;
CREATE TRIGGER screening_run_update BEFORE UPDATE ON screening_run BEGIN
 SELECT CASE WHEN OLD.state='SEALED' OR NEW.screening_run_id IS NOT OLD.screening_run_id OR NEW.project_revision_id IS NOT OLD.project_revision_id OR NEW.discovery_run_id IS NOT OLD.discovery_run_id OR NEW.discovery_sha256 IS NOT OLD.discovery_sha256 OR NEW.frontier_run_id IS NOT OLD.frontier_run_id OR NEW.frontier_sha256 IS NOT OLD.frontier_sha256 OR NEW.plan_json IS NOT OLD.plan_json OR NEW.plan_sha256 IS NOT OLD.plan_sha256 OR NEW.candidate_set_sha256 IS NOT OLD.candidate_set_sha256 OR NEW.assignment_sha256 IS NOT OLD.assignment_sha256 OR NEW.unresolved_discovery_json IS NOT OLD.unresolved_discovery_json OR NEW.unresolved_discovery_sha256 IS NOT OLD.unresolved_discovery_sha256 OR NEW.created_at_utc IS NOT OLD.created_at_utc OR NOT (screening_authorized(OLD.screening_run_id,'WRITE')=1 AND NEW.state!='SEALED' AND NEW.finalization_sha256 IS NULL OR screening_authorized(OLD.screening_run_id,'SEAL')=1 AND OLD.state='RESOLVED' AND NEW.state='SEALED' AND NEW.current_resolution_id IS OLD.current_resolution_id) OR (NEW.current_resolution_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM screening_resolution WHERE resolution_id=NEW.current_resolution_id AND screening_run_id=OLD.screening_run_id)) THEN RAISE(ABORT,'immutable screening transition') END; END;

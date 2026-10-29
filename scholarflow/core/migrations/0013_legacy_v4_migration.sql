-- Historical legacy evidence only; does not authorize screening or corpus.
CREATE TABLE legacy_migration_run (
 migration_id TEXT PRIMARY KEY, source_fingerprint TEXT NOT NULL, plan_json TEXT NOT NULL, plan_sha256 TEXT NOT NULL UNIQUE,
 project_id TEXT REFERENCES review_project(project_id), revision_id TEXT REFERENCES project_revision(revision_id),
 snapshot_id TEXT REFERENCES provider_snapshot(snapshot_id), resolution_run_id TEXT REFERENCES resolution_run(resolution_run_id),
 state TEXT NOT NULL CHECK(state IN ('OPEN','COMPLETE')), manifest_json TEXT, manifest_sha256 TEXT, created_at TEXT NOT NULL
);
CREATE TABLE legacy_migration_artifact (
 migration_id TEXT NOT NULL REFERENCES legacy_migration_run(migration_id), relative_path TEXT NOT NULL,
 inventory_json TEXT NOT NULL, inventory_sha256 TEXT NOT NULL, artifact_sha256 TEXT REFERENCES raw_artifact(artifact_sha256),
 PRIMARY KEY(migration_id,relative_path)
);
CREATE TABLE legacy_migration_record_map (
 migration_id TEXT NOT NULL REFERENCES legacy_migration_run(migration_id), relative_path TEXT NOT NULL, row_ordinal INTEGER NOT NULL,
 provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id), adapter TEXT NOT NULL,
 original_row_sha256 TEXT NOT NULL, PRIMARY KEY(migration_id,relative_path,row_ordinal),
 FOREIGN KEY(migration_id,relative_path) REFERENCES legacy_migration_artifact(migration_id,relative_path)
);
CREATE TABLE legacy_migration_warning (
 migration_id TEXT NOT NULL REFERENCES legacy_migration_run(migration_id), relative_path TEXT NOT NULL, code TEXT NOT NULL,
 PRIMARY KEY(migration_id,relative_path,code), FOREIGN KEY(migration_id,relative_path) REFERENCES legacy_migration_artifact(migration_id,relative_path)
);
CREATE TRIGGER legacy_run_insert BEFORE INSERT ON legacy_migration_run BEGIN
 SELECT CASE WHEN legacy_migration_authorized(NEW.migration_id)!=1 OR NEW.state!='OPEN' OR EXISTS(SELECT 1 FROM legacy_migration_run WHERE migration_id=NEW.migration_id OR plan_sha256=NEW.plan_sha256) THEN RAISE(ABORT,'immutable legacy run') END;
END;
CREATE TRIGGER legacy_run_delete BEFORE DELETE ON legacy_migration_run BEGIN SELECT RAISE(ABORT,'immutable legacy run'); END;
CREATE TRIGGER legacy_run_update BEFORE UPDATE ON legacy_migration_run BEGIN
 SELECT CASE WHEN legacy_migration_authorized(OLD.migration_id)!=1 OR OLD.state!='OPEN' OR NEW.state!='COMPLETE' OR OLD.migration_id!=NEW.migration_id OR OLD.source_fingerprint!=NEW.source_fingerprint OR OLD.plan_json!=NEW.plan_json OR OLD.plan_sha256!=NEW.plan_sha256 OR OLD.created_at!=NEW.created_at OR NEW.manifest_json IS NULL OR NEW.manifest_sha256 IS NULL THEN RAISE(ABORT,'immutable legacy run') END;
END;
CREATE TRIGGER legacy_migration_artifact_insert BEFORE INSERT ON legacy_migration_artifact BEGIN SELECT CASE WHEN legacy_migration_authorized(NEW.migration_id)!=1 OR (SELECT state FROM legacy_migration_run WHERE migration_id=NEW.migration_id)!='OPEN' OR EXISTS(SELECT 1 FROM legacy_migration_artifact WHERE migration_id=NEW.migration_id AND relative_path=NEW.relative_path) THEN RAISE(ABORT,'immutable legacy insert') END; END;
CREATE TRIGGER legacy_migration_artifact_update BEFORE UPDATE ON legacy_migration_artifact BEGIN SELECT RAISE(ABORT,'immutable legacy evidence'); END;
CREATE TRIGGER legacy_migration_artifact_delete BEFORE DELETE ON legacy_migration_artifact BEGIN SELECT RAISE(ABORT,'immutable legacy evidence'); END;
CREATE TRIGGER legacy_migration_record_map_insert BEFORE INSERT ON legacy_migration_record_map BEGIN SELECT CASE WHEN legacy_migration_authorized(NEW.migration_id)!=1 OR (SELECT state FROM legacy_migration_run WHERE migration_id=NEW.migration_id)!='OPEN' OR EXISTS(SELECT 1 FROM legacy_migration_record_map WHERE migration_id=NEW.migration_id AND relative_path=NEW.relative_path AND row_ordinal=NEW.row_ordinal) THEN RAISE(ABORT,'immutable legacy insert') END; END;
CREATE TRIGGER legacy_migration_record_map_update BEFORE UPDATE ON legacy_migration_record_map BEGIN SELECT RAISE(ABORT,'immutable legacy evidence'); END;
CREATE TRIGGER legacy_migration_record_map_delete BEFORE DELETE ON legacy_migration_record_map BEGIN SELECT RAISE(ABORT,'immutable legacy evidence'); END;
CREATE TRIGGER legacy_migration_warning_insert BEFORE INSERT ON legacy_migration_warning BEGIN SELECT CASE WHEN legacy_migration_authorized(NEW.migration_id)!=1 OR (SELECT state FROM legacy_migration_run WHERE migration_id=NEW.migration_id)!='OPEN' OR EXISTS(SELECT 1 FROM legacy_migration_warning WHERE migration_id=NEW.migration_id AND relative_path=NEW.relative_path AND code=NEW.code) THEN RAISE(ABORT,'immutable legacy insert') END; END;
CREATE TRIGGER legacy_migration_warning_update BEFORE UPDATE ON legacy_migration_warning BEGIN SELECT RAISE(ABORT,'immutable legacy evidence'); END;
CREATE TRIGGER legacy_migration_warning_delete BEFORE DELETE ON legacy_migration_warning BEGIN SELECT RAISE(ABORT,'immutable legacy evidence'); END;

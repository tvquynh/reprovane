-- Stage02: immutable typed provider claims, not canonical identity.

-- Calendar-day validity is completed in Python; SQL checks date shape/ranges.

CREATE TABLE publication_metadata_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,4)='pmo_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name)) > 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) > 0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version)) > 0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '????-??-??T??:??:??*Z' AND julianday(normalized_at_utc) IS NOT NULL),
    title TEXT,
    subtitle TEXT,
    abstract_text TEXT,
    publication_type TEXT,
    publication_date TEXT CHECK(publication_date IS NULL OR (publication_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND substr(publication_date,1,4) BETWEEN '0001' AND '9999' AND substr(publication_date,6,2) BETWEEN '01' AND '12' AND substr(publication_date,9,2) BETWEEN '01' AND '31')),
    publication_year INTEGER CHECK(publication_year IS NULL OR (typeof(publication_year)='integer' AND publication_year BETWEEN 1 AND 9999)),
    language_code TEXT,
    volume TEXT,
    issue TEXT,
    first_page TEXT,
    last_page TEXT,
    article_number TEXT,
    publisher_name TEXT,
    field_provenance_json TEXT NOT NULL CHECK(CASE WHEN json_valid(field_provenance_json) THEN json_type(field_provenance_json)='object' ELSE 0 END),
    CHECK(title IS NOT NULL OR subtitle IS NOT NULL OR abstract_text IS NOT NULL OR publication_type IS NOT NULL OR publication_date IS NOT NULL OR publication_year IS NOT NULL OR language_code IS NOT NULL OR volume IS NOT NULL OR issue IS NOT NULL OR first_page IS NOT NULL OR last_page IS NOT NULL OR article_number IS NOT NULL OR publisher_name IS NOT NULL)
);

CREATE INDEX pmo_provider_record ON publication_metadata_observation(provider_record_id);

CREATE TRIGGER publication_metadata_observation_sealed_record BEFORE INSERT ON publication_metadata_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM provider_record AS r JOIN provider_snapshot AS s ON s.snapshot_id=r.snapshot_id
        WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL
    ) THEN RAISE(ABORT, 'observation requires sealed provider record') END;
END;

CREATE TRIGGER publication_metadata_observation_no_update BEFORE UPDATE ON publication_metadata_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER publication_metadata_observation_no_delete BEFORE DELETE ON publication_metadata_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER publication_metadata_observation_no_replace BEFORE INSERT ON publication_metadata_observation
WHEN EXISTS (SELECT 1 FROM publication_metadata_observation WHERE observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT, 'immutable observation identity'); END;

CREATE TABLE publication_identifier_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,4)='pio_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name)) > 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) > 0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version)) > 0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '????-??-??T??:??:??*Z' AND julianday(normalized_at_utc) IS NOT NULL),
    identifier_namespace TEXT NOT NULL CHECK(length(identifier_namespace) > 0 AND substr(identifier_namespace,1,1) GLOB '[a-z]' AND identifier_namespace NOT GLOB '*[^a-z0-9_.-]*'),
    identifier_value_original TEXT NOT NULL CHECK(length(trim(identifier_value_original)) > 0),
    identifier_value_normalized TEXT NOT NULL CHECK(length(trim(identifier_value_normalized)) > 0),
    source_field_path TEXT
);

CREATE INDEX pio_provider_record ON publication_identifier_observation(provider_record_id);

CREATE TRIGGER publication_identifier_observation_sealed_record BEFORE INSERT ON publication_identifier_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM provider_record AS r JOIN provider_snapshot AS s ON s.snapshot_id=r.snapshot_id
        WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL
    ) THEN RAISE(ABORT, 'observation requires sealed provider record') END;
END;

CREATE TRIGGER publication_identifier_observation_no_update BEFORE UPDATE ON publication_identifier_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER publication_identifier_observation_no_delete BEFORE DELETE ON publication_identifier_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER publication_identifier_observation_no_replace BEFORE INSERT ON publication_identifier_observation
WHEN EXISTS (SELECT 1 FROM publication_identifier_observation WHERE observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT, 'immutable observation identity'); END;

CREATE TABLE venue_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,3)='vo_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name)) > 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) > 0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version)) > 0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '????-??-??T??:??:??*Z' AND julianday(normalized_at_utc) IS NOT NULL),
    title TEXT,
    publisher_name TEXT,
    source_type TEXT,
    country_code TEXT,
    field_provenance_json TEXT NOT NULL CHECK(CASE WHEN json_valid(field_provenance_json) THEN json_type(field_provenance_json)='object' ELSE 0 END),
    CHECK(title IS NOT NULL OR publisher_name IS NOT NULL OR source_type IS NOT NULL OR country_code IS NOT NULL)
);

CREATE INDEX vo_provider_record ON venue_observation(provider_record_id);

CREATE TRIGGER venue_observation_sealed_record BEFORE INSERT ON venue_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM provider_record AS r JOIN provider_snapshot AS s ON s.snapshot_id=r.snapshot_id
        WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL
    ) THEN RAISE(ABORT, 'observation requires sealed provider record') END;
END;

CREATE TRIGGER venue_observation_no_update BEFORE UPDATE ON venue_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER venue_observation_no_delete BEFORE DELETE ON venue_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER venue_observation_no_replace BEFORE INSERT ON venue_observation
WHEN EXISTS (SELECT 1 FROM venue_observation WHERE observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT, 'immutable observation identity'); END;

CREATE TABLE venue_identifier_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,4)='vio_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name)) > 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) > 0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version)) > 0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '????-??-??T??:??:??*Z' AND julianday(normalized_at_utc) IS NOT NULL),
    identifier_namespace TEXT NOT NULL CHECK(length(identifier_namespace) > 0 AND substr(identifier_namespace,1,1) GLOB '[a-z]' AND identifier_namespace NOT GLOB '*[^a-z0-9_.-]*'),
    identifier_value_original TEXT NOT NULL CHECK(length(trim(identifier_value_original)) > 0),
    identifier_value_normalized TEXT NOT NULL CHECK(length(trim(identifier_value_normalized)) > 0),
    source_field_path TEXT
);

CREATE INDEX vio_provider_record ON venue_identifier_observation(provider_record_id);

CREATE TRIGGER venue_identifier_observation_sealed_record BEFORE INSERT ON venue_identifier_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM provider_record AS r JOIN provider_snapshot AS s ON s.snapshot_id=r.snapshot_id
        WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL
    ) THEN RAISE(ABORT, 'observation requires sealed provider record') END;
END;

CREATE TRIGGER venue_identifier_observation_no_update BEFORE UPDATE ON venue_identifier_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER venue_identifier_observation_no_delete BEFORE DELETE ON venue_identifier_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER venue_identifier_observation_no_replace BEFORE INSERT ON venue_identifier_observation
WHEN EXISTS (SELECT 1 FROM venue_identifier_observation WHERE observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT, 'immutable observation identity'); END;

CREATE TABLE venue_membership_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,4)='vmo_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name)) > 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) > 0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version)) > 0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '????-??-??T??:??:??*Z' AND julianday(normalized_at_utc) IS NOT NULL),
    membership_namespace TEXT NOT NULL CHECK(length(membership_namespace) > 0 AND substr(membership_namespace,1,1) GLOB '[a-z]' AND membership_namespace NOT GLOB '*[^a-z0-9_.-]*'),
    collection_code TEXT,
    status_code TEXT,
    coverage_start_year INTEGER CHECK(coverage_start_year IS NULL OR (typeof(coverage_start_year)='integer' AND coverage_start_year BETWEEN 1 AND 9999)),
    coverage_end_year INTEGER CHECK(coverage_end_year IS NULL OR (typeof(coverage_end_year)='integer' AND coverage_end_year BETWEEN 1 AND 9999)),
    valid_from_date TEXT CHECK(valid_from_date IS NULL OR (valid_from_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND substr(valid_from_date,1,4) BETWEEN '0001' AND '9999' AND substr(valid_from_date,6,2) BETWEEN '01' AND '12' AND substr(valid_from_date,9,2) BETWEEN '01' AND '31')),
    valid_to_date TEXT CHECK(valid_to_date IS NULL OR (valid_to_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND substr(valid_to_date,1,4) BETWEEN '0001' AND '9999' AND substr(valid_to_date,6,2) BETWEEN '01' AND '12' AND substr(valid_to_date,9,2) BETWEEN '01' AND '31')),
    source_field_path TEXT,
    CHECK(collection_code IS NOT NULL OR status_code IS NOT NULL),
    CHECK(coverage_start_year IS NULL OR coverage_end_year IS NULL OR coverage_start_year <= coverage_end_year)
);

CREATE INDEX vmo_provider_record ON venue_membership_observation(provider_record_id);

CREATE TRIGGER venue_membership_observation_sealed_record BEFORE INSERT ON venue_membership_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM provider_record AS r JOIN provider_snapshot AS s ON s.snapshot_id=r.snapshot_id
        WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL
    ) THEN RAISE(ABORT, 'observation requires sealed provider record') END;
END;

CREATE TRIGGER venue_membership_observation_no_update BEFORE UPDATE ON venue_membership_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER venue_membership_observation_no_delete BEFORE DELETE ON venue_membership_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER venue_membership_observation_no_replace BEFORE INSERT ON venue_membership_observation
WHEN EXISTS (SELECT 1 FROM venue_membership_observation WHERE observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT, 'immutable observation identity'); END;

CREATE TABLE venue_metric_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,5)='vmeo_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name)) > 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) > 0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version)) > 0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '????-??-??T??:??:??*Z' AND julianday(normalized_at_utc) IS NOT NULL),
    metric_namespace TEXT NOT NULL CHECK(length(metric_namespace) > 0 AND substr(metric_namespace,1,1) GLOB '[a-z]' AND metric_namespace NOT GLOB '*[^a-z0-9_.-]*'),
    metric_code TEXT NOT NULL CHECK(length(trim(metric_code)) > 0),
    period_year INTEGER CHECK(period_year IS NULL OR (typeof(period_year)='integer' AND period_year BETWEEN 1 AND 9999)),
    category_name TEXT,
    value_type TEXT NOT NULL CHECK(value_type IN ('TEXT','INTEGER','REAL')),
    value_text TEXT,
    value_integer INTEGER CHECK(value_integer IS NULL OR typeof(value_integer)='integer'),
    value_real REAL CHECK(value_real IS NULL OR (typeof(value_real)='real' AND value_real BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308)),
    unit TEXT,
    source_field_path TEXT,
    CONSTRAINT metric_typed_value CHECK((value_type='TEXT' AND value_text IS NOT NULL AND value_integer IS NULL AND value_real IS NULL) OR (value_type='INTEGER' AND value_integer IS NOT NULL AND value_text IS NULL AND value_real IS NULL) OR (value_type='REAL' AND value_real IS NOT NULL AND value_text IS NULL AND value_integer IS NULL))
);

CREATE INDEX vmeo_provider_record ON venue_metric_observation(provider_record_id);

CREATE TRIGGER venue_metric_observation_sealed_record BEFORE INSERT ON venue_metric_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM provider_record AS r JOIN provider_snapshot AS s ON s.snapshot_id=r.snapshot_id
        WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL
    ) THEN RAISE(ABORT, 'observation requires sealed provider record') END;
END;

CREATE TRIGGER venue_metric_observation_no_update BEFORE UPDATE ON venue_metric_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER venue_metric_observation_no_delete BEFORE DELETE ON venue_metric_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER venue_metric_observation_no_replace BEFORE INSERT ON venue_metric_observation
WHEN EXISTS (SELECT 1 FROM venue_metric_observation WHERE observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT, 'immutable observation identity'); END;

CREATE TABLE citation_edge_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,4)='ceo_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name)) > 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) > 0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version)) > 0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '????-??-??T??:??:??*Z' AND julianday(normalized_at_utc) IS NOT NULL),
    source_identifier_namespace TEXT NOT NULL CHECK(length(source_identifier_namespace) > 0 AND substr(source_identifier_namespace,1,1) GLOB '[a-z]' AND source_identifier_namespace NOT GLOB '*[^a-z0-9_.-]*'),
    source_identifier_value TEXT NOT NULL CHECK(length(trim(source_identifier_value)) > 0),
    target_identifier_namespace TEXT NOT NULL CHECK(length(target_identifier_namespace) > 0 AND substr(target_identifier_namespace,1,1) GLOB '[a-z]' AND target_identifier_namespace NOT GLOB '*[^a-z0-9_.-]*'),
    target_identifier_value TEXT NOT NULL CHECK(length(trim(target_identifier_value)) > 0),
    relation_type TEXT NOT NULL CHECK(relation_type='CITES'),
    source_field_path TEXT
);

CREATE INDEX ceo_provider_record ON citation_edge_observation(provider_record_id);

CREATE TRIGGER citation_edge_observation_sealed_record BEFORE INSERT ON citation_edge_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM provider_record AS r JOIN provider_snapshot AS s ON s.snapshot_id=r.snapshot_id
        WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL
    ) THEN RAISE(ABORT, 'observation requires sealed provider record') END;
END;

CREATE TRIGGER citation_edge_observation_no_update BEFORE UPDATE ON citation_edge_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER citation_edge_observation_no_delete BEFORE DELETE ON citation_edge_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER citation_edge_observation_no_replace BEFORE INSERT ON citation_edge_observation
WHEN EXISTS (SELECT 1 FROM citation_edge_observation WHERE observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT, 'immutable observation identity'); END;

CREATE TABLE integrity_event_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,4)='ieo_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name)) > 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) > 0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version)) > 0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '????-??-??T??:??:??*Z' AND julianday(normalized_at_utc) IS NOT NULL),
    event_type TEXT NOT NULL CHECK(event_type IN ('RETRACTION','CORRECTION','EXPRESSION_OF_CONCERN','WITHDRAWAL','REMOVAL','OTHER')),
    subject_identifier_namespace TEXT NOT NULL CHECK(length(subject_identifier_namespace) > 0 AND substr(subject_identifier_namespace,1,1) GLOB '[a-z]' AND subject_identifier_namespace NOT GLOB '*[^a-z0-9_.-]*'),
    subject_identifier_value TEXT NOT NULL CHECK(length(trim(subject_identifier_value)) > 0),
    related_notice_identifier_namespace TEXT NULL CHECK(related_notice_identifier_namespace IS NULL OR (length(related_notice_identifier_namespace) > 0 AND substr(related_notice_identifier_namespace,1,1) GLOB '[a-z]' AND related_notice_identifier_namespace NOT GLOB '*[^a-z0-9_.-]*')),
    related_notice_identifier_value TEXT,
    effective_date TEXT CHECK(effective_date IS NULL OR (effective_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND substr(effective_date,1,4) BETWEEN '0001' AND '9999' AND substr(effective_date,6,2) BETWEEN '01' AND '12' AND substr(effective_date,9,2) BETWEEN '01' AND '31')),
    status_code TEXT,
    source_field_path TEXT,
    CONSTRAINT integrity_notice_pair CHECK((related_notice_identifier_namespace IS NULL) = (related_notice_identifier_value IS NULL))
);

CREATE INDEX ieo_provider_record ON integrity_event_observation(provider_record_id);

CREATE TRIGGER integrity_event_observation_sealed_record BEFORE INSERT ON integrity_event_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM provider_record AS r JOIN provider_snapshot AS s ON s.snapshot_id=r.snapshot_id
        WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL
    ) THEN RAISE(ABORT, 'observation requires sealed provider record') END;
END;

CREATE TRIGGER integrity_event_observation_no_update BEFORE UPDATE ON integrity_event_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER integrity_event_observation_no_delete BEFORE DELETE ON integrity_event_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER integrity_event_observation_no_replace BEFORE INSERT ON integrity_event_observation
WHEN EXISTS (SELECT 1 FROM integrity_event_observation WHERE observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT, 'immutable observation identity'); END;

CREATE INDEX pio_identifier ON publication_identifier_observation(identifier_namespace,identifier_value_normalized);

CREATE INDEX vio_identifier ON venue_identifier_observation(identifier_namespace,identifier_value_normalized);

CREATE INDEX vmo_namespace ON venue_membership_observation(membership_namespace);

CREATE INDEX vmeo_metric_period ON venue_metric_observation(metric_namespace,metric_code,period_year);

CREATE INDEX ceo_source ON citation_edge_observation(source_identifier_namespace,source_identifier_value);

CREATE INDEX ceo_target ON citation_edge_observation(target_identifier_namespace,target_identifier_value);

CREATE INDEX ieo_subject ON integrity_event_observation(subject_identifier_namespace,subject_identifier_value);

-- Classifications/languages/discontinuous coverage are attributes, not metrics.
CREATE TABLE venue_provider_attribute_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,4)='vpa_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    attribute_namespace TEXT NOT NULL CHECK(attribute_namespace IN ('scopus','wos')),
    attribute_code TEXT NOT NULL CHECK(attribute_code IN ('ASJC','CATEGORIES','COVERAGE','LANGUAGES','DISCONTINUED','RAW_STATUS','PUBLISHER_ADDRESS')),
    value_json TEXT NOT NULL CHECK(CASE WHEN json_valid(value_json) THEN json_type(value_json)='object' ELSE 0 END),
    source_field_path TEXT NOT NULL CHECK(length(trim(source_field_path))>0),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name))>0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version))>0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version))>0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '*Z' AND julianday(normalized_at_utc) IS NOT NULL),
    UNIQUE(provider_record_id,attribute_namespace,attribute_code,value_json,normalizer_name,normalizer_version,observation_schema_version)
);
CREATE INDEX vpa_provider_record ON venue_provider_attribute_observation(provider_record_id);
CREATE TRIGGER vpa_sealed_record BEFORE INSERT ON venue_provider_attribute_observation
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM provider_record r JOIN provider_snapshot s ON s.snapshot_id=r.snapshot_id
 WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL)
 THEN RAISE(ABORT,'attribute requires sealed provider record') END;
END;
CREATE TRIGGER vpa_no_update BEFORE UPDATE ON venue_provider_attribute_observation
BEGIN SELECT RAISE(ABORT,'immutable provider attribute'); END;
CREATE TRIGGER vpa_no_delete BEFORE DELETE ON venue_provider_attribute_observation
BEGIN SELECT RAISE(ABORT,'immutable provider attribute'); END;
CREATE TRIGGER vpa_no_replace BEFORE INSERT ON venue_provider_attribute_observation
WHEN EXISTS(SELECT 1 FROM venue_provider_attribute_observation WHERE observation_id=NEW.observation_id OR
 (provider_record_id=NEW.provider_record_id AND attribute_namespace=NEW.attribute_namespace AND attribute_code=NEW.attribute_code
 AND value_json=NEW.value_json AND normalizer_name=NEW.normalizer_name AND normalizer_version=NEW.normalizer_version AND observation_schema_version=NEW.observation_schema_version))
BEGIN SELECT RAISE(ABORT,'immutable provider attribute identity'); END;
CREATE UNIQUE INDEX stage06_import_identity ON provider_snapshot(json_extract(metadata_json,'$.stage06_import_key'))
WHERE parser_name='venue-list-stage06';

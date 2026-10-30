-- One provider-observed authorship position; no person or publication identity.
CREATE TABLE publication_authorship_observation (
    observation_id TEXT PRIMARY KEY NOT NULL CHECK(substr(observation_id,1,4)='pao_'),
    provider_record_id TEXT NOT NULL REFERENCES provider_record(provider_record_id),
    author_position INTEGER NOT NULL CHECK(typeof(author_position)='integer' AND author_position >= 1),
    author_display_name TEXT NOT NULL CHECK(length(trim(author_display_name)) > 0),
    author_given_name TEXT,
    author_family_name TEXT,
    provider_author_id_namespace TEXT CHECK(provider_author_id_namespace IS NULL OR
        (length(provider_author_id_namespace) > 0 AND substr(provider_author_id_namespace,1,1) GLOB '[a-z]'
        AND provider_author_id_namespace NOT GLOB '*[^a-z0-9_.-]*')),
    provider_author_id_original TEXT CHECK(provider_author_id_original IS NULL OR length(trim(provider_author_id_original)) > 0),
    provider_author_id_normalized TEXT CHECK(provider_author_id_normalized IS NULL OR length(trim(provider_author_id_normalized)) > 0),
    orcid_original TEXT CHECK(orcid_original IS NULL OR length(trim(orcid_original)) > 0),
    orcid_normalized TEXT CHECK(orcid_normalized IS NULL OR
        orcid_normalized GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9X]'),
    source_field_path TEXT,
    field_provenance_json TEXT NOT NULL CHECK(CASE WHEN json_valid(field_provenance_json)
        THEN json_type(field_provenance_json)='object' ELSE 0 END),
    normalizer_name TEXT NOT NULL CHECK(length(trim(normalizer_name)) > 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) > 0),
    observation_schema_version TEXT NOT NULL CHECK(length(trim(observation_schema_version)) > 0),
    normalization_config_sha256 TEXT CHECK(normalization_config_sha256 IS NULL OR
        (length(normalization_config_sha256)=64 AND normalization_config_sha256 NOT GLOB '*[^0-9a-f]*')),
    normalized_at_utc TEXT NOT NULL CHECK(normalized_at_utc GLOB '????-??-??T??:??:??*Z'
        AND julianday(normalized_at_utc) IS NOT NULL),
    CONSTRAINT authorship_provider_id_group CHECK(
        (provider_author_id_namespace IS NULL AND provider_author_id_original IS NULL AND provider_author_id_normalized IS NULL)
        OR (provider_author_id_namespace IS NOT NULL AND provider_author_id_original IS NOT NULL AND provider_author_id_normalized IS NOT NULL)),
    CONSTRAINT authorship_orcid_pair CHECK((orcid_original IS NULL) = (orcid_normalized IS NULL))
);

CREATE INDEX pao_record_position ON publication_authorship_observation(provider_record_id, author_position);
CREATE INDEX pao_provider_author_id ON publication_authorship_observation(provider_author_id_namespace, provider_author_id_normalized);
CREATE INDEX pao_orcid ON publication_authorship_observation(orcid_normalized);

CREATE TRIGGER publication_authorship_observation_sealed_record BEFORE INSERT ON publication_authorship_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM provider_record AS r JOIN provider_snapshot AS s ON s.snapshot_id=r.snapshot_id
        WHERE r.provider_record_id=NEW.provider_record_id AND s.sealed_at_utc IS NOT NULL
    ) THEN RAISE(ABORT, 'observation requires sealed provider record') END;
END;

CREATE TRIGGER publication_authorship_observation_no_update BEFORE UPDATE ON publication_authorship_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER publication_authorship_observation_no_delete BEFORE DELETE ON publication_authorship_observation
BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;

CREATE TRIGGER publication_authorship_observation_no_replace BEFORE INSERT ON publication_authorship_observation
WHEN EXISTS (SELECT 1 FROM publication_authorship_observation WHERE observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT, 'immutable observation identity'); END;

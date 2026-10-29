"""Offline foundation contracts; no legacy fixtures or network are used."""
import hashlib
from contextlib import closing
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scholarflow.core import (ArtifactIntegrityError, FoundationError, ImmutabilityError, MigrationDriftError,
                           MigrationError, SnapshotSealedError, ValidationError)
from scholarflow.core.artifacts import StoredArtifact
from scholarflow.core.db import Database
from scholarflow.core.foundation import Foundation
from scholarflow.core.migrations import MIGRATION_DIR
from scholarflow.core.provenance import SENSITIVE_QUERY_KEYS, canonical_json, sanitize_locator


class FoundationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "workspace")
        self.f = Foundation(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def provider(self):
        return self.f.register_provider("fixture", "Synthetic provider")

    def snapshot(self, provider=None, **kwargs):
        provider = provider or self.provider()
        return self.f.create_provider_snapshot(provider["provider_id"], "synthetic", "API", parser_name="fixture",
            parser_version="1", schema_profile="opaque", schema_version="1", **kwargs)

    def attached(self, snapshot=None, data=b'{"results":[]}'):
        snapshot = snapshot or self.snapshot()
        a = self.f.artifacts.put_bytes(data)
        self.f.register_raw_artifact(a)
        self.f.attach_artifact_to_snapshot(snapshot["snapshot_id"], a.artifact_sha256, "response", 0)
        return snapshot, a

    def record(self):
        s, a = self.attached()
        r = self.f.add_provider_record(s["snapshot_id"], a.artifact_sha256, "/results/0", provider_native_id="native1")
        return s, a, r

    def denied(self, sql, params=()):
        # Raw sqlite3 connection deliberately bypasses Database.execute and service validation.
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute(sql, params)

    def test_fresh_database(self):
        rows = self.db.execute("SELECT * FROM schema_migration").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sha256"], hashlib.sha256((MIGRATION_DIR / rows[0]["name"]).read_bytes()).hexdigest())

    def test_reopen_noop(self):
        before = tuple(self.db.execute("SELECT * FROM schema_migration").fetchone())
        with Database(self.db.workspace) as second:
            self.assertEqual(tuple(second.execute("SELECT * FROM schema_migration").fetchone()), before)
            self.assertEqual(second.connection.total_changes, 0)

    def test_migration_checksum_drift(self):
        copy = self.root / "migrations"
        shutil.copytree(MIGRATION_DIR, copy)
        path = copy / "0001_persistence_foundation.sql"
        path.write_bytes(path.read_bytes() + b"\n-- deliberate drift\n")
        with self.assertRaises(MigrationDriftError):
            Database(self.db.workspace, migration_dir=copy)

    def test_failed_migration_rollback(self):
        copy = self.root / "bad_migrations"
        shutil.copytree(MIGRATION_DIR, copy)
        (copy / "0002_fail.sql").write_text("CREATE TABLE should_rollback(x);\nINSERT INTO absent VALUES (1);", encoding="utf-8")
        workspace = self.root / "failed"
        with self.assertRaises(MigrationError):
            Database(workspace, migration_dir=copy)
        with closing(sqlite3.connect(workspace / "db/litintel.sqlite")) as raw:
            self.assertEqual(raw.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0)
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_failed_upgrade_preserves_applied_ledger(self):
        copy = self.root / "upgrade"
        shutil.copytree(MIGRATION_DIR, copy)
        (copy / "0002_fail.sql").write_text("CREATE TABLE should_rollback(x);\nBROKEN;", encoding="utf-8")
        with self.assertRaises(MigrationError):
            Database(self.db.workspace, migration_dir=copy)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM schema_migration").fetchone()[0], 1)
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone())

    def test_user_version(self):
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_required_tables_and_triggers(self):
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"schema_migration", "review_project", "project_revision", "provider",
            "provider_snapshot", "raw_artifact", "provider_snapshot_artifact", "provider_record", "audit_event"})
        guards = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertTrue({"provider_record_no_update", "project_revision_no_update", "snapshot_update_guard",
                         "provider_record_open", "snapshot_artifact_open"} <= guards)

    def test_connection_pragmas(self):
        for name, expected in [("foreign_keys", 1), ("journal_mode", "wal"), ("synchronous", 2),
                               ("busy_timeout", 5000), ("recursive_triggers", 1)]:
            self.assertEqual(self.db.execute("PRAGMA " + name).fetchone()[0], expected)

    def test_commit_success(self):
        with self.db.transaction():
            p = self.f.create_project("commit", "Commit")
        with Database(self.db.workspace) as other:
            self.assertEqual(Foundation(other).get_project(p["project_id"])["project_key"], "commit")

    def test_transaction_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.f.create_project("rollback", "Rollback")
                self.provider()
                raise RuntimeError("synthetic failure")
        self.assertEqual(self.db.execute("SELECT count(*) FROM review_project").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM provider").fetchone()[0], 0)

    def test_nested_transaction_clear_error(self):
        with self.db.transaction():
            with self.assertRaisesRegex(FoundationError, "Nested"):
                with self.db.transaction():
                    pass

    def test_foreign_keys_enforced(self):
        with self.assertRaises(ValidationError):
            self.f.create_project_revision("missing", 1, "yaml", "x: y")

    def test_project_creation(self):
        p = self.f.create_project("review", "Review")
        self.assertEqual(self.f.get_project(p["project_id"]), p)
        self.assertTrue(p["project_id"].startswith("prj_"))
        self.assertTrue(p["created_at_utc"].endswith("Z"))

    def test_duplicate_project_key(self):
        self.f.create_project("same", "First")
        with self.assertRaises(ValidationError):
            self.f.create_project("same", "Second")

    def test_exact_manifest_text(self):
        p = self.f.create_project("review", "Review")
        text = '\ufeffname: "Tiếng Việt"\r\n# keep spacing  \r\n'
        rev = self.f.create_project_revision(p["project_id"], 1, "yaml", text)
        self.assertEqual(self.f.get_project_revision(rev["revision_id"])["manifest_text"], text)
        self.assertEqual(rev["manifest_sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())

    def test_revision_hash_exact_not_semantic(self):
        p = self.f.create_project("review", "Review")
        a = self.f.create_project_revision(p["project_id"], 1, "json", '{"x":1}')
        b = self.f.create_project_revision(p["project_id"], 2, "json", '{ "x": 1 }', parent_revision_id=a["revision_id"])
        self.assertNotEqual(a["manifest_sha256"], b["manifest_sha256"])
        self.assertEqual(self.f.list_project_revisions(p["project_id"]), [a, b])

    def test_revision_update_rejected(self):
        p = self.f.create_project("review", "Review")
        rev = self.f.create_project_revision(p["project_id"], 1, "yaml", "x: 1")
        self.denied("UPDATE project_revision SET manifest_format='json' WHERE revision_id=?", (rev["revision_id"],))

    def test_revision_delete_rejected(self):
        p = self.f.create_project("review", "Review")
        rev = self.f.create_project_revision(p["project_id"], 1, "yaml", "x: 1")
        self.denied("DELETE FROM project_revision WHERE revision_id=?", (rev["revision_id"],))

    def test_duplicate_revision_number(self):
        p = self.f.create_project("review", "Review")
        self.f.create_project_revision(p["project_id"], 1, "text", "a")
        with self.assertRaises(ValidationError):
            self.f.create_project_revision(p["project_id"], 1, "text", "b")

    def test_cross_project_parent_rejected(self):
        a = self.f.create_project("a", "A")
        b = self.f.create_project("b", "B")
        parent = self.f.create_project_revision(a["project_id"], 1, "text", "a")
        with self.assertRaises(ValidationError):
            self.f.create_project_revision(b["project_id"], 2, "text", "b", parent_revision_id=parent["revision_id"])

    def test_revision_hash_constraint_direct_sql(self):
        p = self.f.create_project("review", "Review")
        self.denied("INSERT INTO project_revision VALUES ('rev_bad',?,1,NULL,'text','x',?,'2026-01-01T00:00:00Z')",
                    (p["project_id"], "0" * 64))

    def test_provider_creation(self):
        p = self.provider()
        self.assertEqual(self.f.get_provider(p["provider_id"]), p)
        with self.assertRaises(ValidationError):
            self.provider()

    def test_snapshot_starts_open(self):
        s = self.snapshot()
        self.assertIsNone(s["sealed_at_utc"])
        self.assertIsNone(self.f.get_provider_snapshot(s["snapshot_id"])["content_manifest_sha256"])

    def test_acquisition_mode_validation(self):
        p = self.provider()
        with self.assertRaises(ValidationError):
            self.f.create_provider_snapshot(p["provider_id"], "x", "INVALID", parser_name="p", parser_version="1",
                                             schema_profile="x", schema_version="1")

    def test_snapshot_seal_and_verify(self):
        s, a, r = self.record()
        sealed = self.f.seal_provider_snapshot(s["snapshot_id"])
        self.assertEqual(len(sealed["content_manifest_sha256"]), 64)
        self.assertTrue(sealed["sealed_at_utc"].endswith("Z"))
        self.assertTrue(self.f.verify_provider_snapshot(s["snapshot_id"]))

    def test_sealed_update_delete_rejected(self):
        s = self.snapshot()
        self.f.seal_provider_snapshot(s["snapshot_id"])
        self.denied("UPDATE provider_snapshot SET dataset='changed'")
        self.denied("DELETE FROM provider_snapshot")

    def test_open_snapshot_cannot_mutate_or_direct_seal(self):
        self.snapshot()
        self.denied("UPDATE provider_snapshot SET dataset='changed'")
        self.denied("UPDATE provider_snapshot SET content_manifest_sha256=?, sealed_at_utc='2026-01-01T00:00:00Z'",
                    ("0" * 64,))
        self.denied("DELETE FROM provider_snapshot")

    def test_sealed_artifact_insert_rejected(self):
        s, a = self.attached()
        self.f.seal_provider_snapshot(s["snapshot_id"])
        self.denied("INSERT INTO provider_snapshot_artifact VALUES (?,?,'extra',1,NULL,'{}','{}','2026-01-01T00:00:00Z')",
                    (s["snapshot_id"], a.artifact_sha256))

    def test_sealed_record_insert_rejected(self):
        s, a = self.attached()
        self.f.seal_provider_snapshot(s["snapshot_id"])
        self.denied("INSERT INTO provider_record VALUES ('rec_extra',?,'same',?,'/new',0,NULL,'2026-01-01T00:00:00Z','{}')",
                    (s["snapshot_id"], a.artifact_sha256))

    def test_service_sealed_error_taxonomy(self):
        s, a = self.attached()
        self.f.seal_provider_snapshot(s["snapshot_id"])
        with self.assertRaises(SnapshotSealedError):
            self.f.add_provider_record(s["snapshot_id"], a.artifact_sha256, "/later")
        with self.assertRaises(SnapshotSealedError):
            self.f.attach_artifact_to_snapshot(s["snapshot_id"], a.artifact_sha256, "extra", 1)

    def test_manifest_deterministic_order(self):
        s, a, r = self.record()
        b = self.f.artifacts.put_bytes(b"second")
        self.f.register_raw_artifact(b)
        self.f.attach_artifact_to_snapshot(s["snapshot_id"], b.artifact_sha256, "response", 1)
        self.f.add_provider_record(s["snapshot_id"], b.artifact_sha256, "/results/1")
        links = [dict(x) for x in self.db.execute("SELECT * FROM provider_snapshot_artifact")]
        records = self.f.list_provider_records(s["snapshot_id"])
        first = self.f.content_manifest(s, links, records)
        second = self.f.content_manifest(s, list(reversed(links)), list(reversed(records)))
        self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))
        expected = hashlib.sha256(first.encode("utf-8")).hexdigest()
        self.assertEqual(self.f.seal_provider_snapshot(s["snapshot_id"])["content_manifest_sha256"], expected)

    def test_second_seal_rejected(self):
        s = self.snapshot()
        self.f.seal_provider_snapshot(s["snapshot_id"])
        with self.assertRaises(SnapshotSealedError):
            self.f.seal_provider_snapshot(s["snapshot_id"])

    def test_seal_checks_bytes_before_update(self):
        s, a = self.attached()
        self.f.artifacts.path_for_hash(a.artifact_sha256).unlink()
        with self.assertRaises(ArtifactIntegrityError):
            self.f.seal_provider_snapshot(s["snapshot_id"])
        self.assertIsNone(self.f.get_provider_snapshot(s["snapshot_id"])["sealed_at_utc"])

    def test_verify_detects_tamper_after_seal(self):
        s, a = self.attached()
        self.f.seal_provider_snapshot(s["snapshot_id"])
        self.f.artifacts.path_for_hash(a.artifact_sha256).write_bytes(b"tamper")
        with self.assertRaises(ArtifactIntegrityError):
            self.f.verify_provider_snapshot(s["snapshot_id"])

    def test_put_bytes_content_address(self):
        data = b"exact\r\n\x00bytes"
        a = self.f.artifacts.put_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(a.artifact_sha256, digest)
        self.assertEqual(a.relative_path, f"raw/sha256/{digest[:2]}/{digest}")
        self.assertEqual(self.f.artifacts.path_for_hash(digest).read_bytes(), data)
        self.assertEqual(self.f.artifacts.verify(digest), a)

    def test_duplicate_bytes_reuse_object(self):
        a = self.f.artifacts.put_bytes(b"same")
        before = self.f.artifacts.path_for_hash(a.artifact_sha256).stat().st_mtime_ns
        self.assertEqual(self.f.artifacts.put_bytes(b"same"), a)
        self.assertEqual(self.f.artifacts.path_for_hash(a.artifact_sha256).stat().st_mtime_ns, before)
        self.assertEqual(len([p for p in self.f.artifacts.root.rglob('*') if p.is_file()]), 1)

    def test_put_file(self):
        path = self.root / "input.bin"
        path.write_bytes(b"\xff\x00\r\n")
        self.assertEqual(self.f.artifacts.put_file(path), self.f.artifacts.put_bytes(path.read_bytes()))

    def test_artifact_tampering_detected(self):
        a = self.f.artifacts.put_bytes(b"original")
        self.f.artifacts.path_for_hash(a.artifact_sha256).write_bytes(b"changed")
        with self.assertRaises(ArtifactIntegrityError):
            self.f.artifacts.verify(a.artifact_sha256)

    def test_corrupt_existing_hash_never_repaired_silently(self):
        a = self.f.artifacts.put_bytes(b"original")
        self.f.artifacts.path_for_hash(a.artifact_sha256).write_bytes(b"changed")
        with self.assertRaises(ArtifactIntegrityError):
            self.f.artifacts.put_bytes(b"original")
        self.assertEqual(self.f.artifacts.path_for_hash(a.artifact_sha256).read_bytes(), b"changed")

    def test_hash_traversal_rejected(self):
        for invalid in ['../escape', 'A'*64, 'a'*63, 'z'*64]:
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self.f.artifacts.path_for_hash(invalid)

    def test_symlink_escape_rejected_when_supported(self):
        outside = self.root / "outside"
        outside.mkdir()
        raw = self.db.workspace / "raw"
        try:
            raw.symlink_to(outside, target_is_directory=True)
        except OSError:
            # Windows developer-mode/admin privilege may be unavailable; path traversal still tested.
            return
        with self.assertRaises(ValidationError):
            self.f.artifacts.put_bytes(b"escape")
        self.assertFalse((outside / "sha256").exists())

    def test_raw_artifact_update_delete_rejected(self):
        a = self.f.artifacts.put_bytes(b"data")
        self.f.register_raw_artifact(a)
        self.denied("UPDATE raw_artifact SET media_type='bad'")
        self.denied("DELETE FROM raw_artifact")

    def test_registration_verifies_descriptor_and_reuses_row(self):
        a = self.f.artifacts.put_bytes(b"data")
        row = self.f.register_raw_artifact(a)
        self.assertEqual(self.f.register_raw_artifact(a), row)
        with self.assertRaises(ArtifactIntegrityError):
            self.f.register_raw_artifact(StoredArtifact(a.artifact_sha256, 999, a.relative_path))

    def test_link_update_delete_rejected(self):
        self.attached()
        self.denied("UPDATE provider_snapshot_artifact SET role='bad'")
        self.denied("DELETE FROM provider_snapshot_artifact")

    def test_record_only_in_open_snapshot_and_attached_artifact(self):
        s, a, r = self.record()
        self.assertEqual(self.f.list_provider_records(s["snapshot_id"]), [r])
        other = self.f.artifacts.put_bytes(b"unattached")
        self.f.register_raw_artifact(other)
        with self.assertRaises(ValidationError):
            self.f.add_provider_record(s["snapshot_id"], other.artifact_sha256, "/unattached")

    def test_provider_record_update_rejected(self):
        self.record()
        self.denied("UPDATE provider_record SET provider_native_id='changed'")

    def test_provider_record_delete_rejected(self):
        self.record()
        self.denied("DELETE FROM provider_record")

    def test_duplicate_snapshot_locator_rejected(self):
        s, a, r = self.record()
        with self.assertRaises(ValidationError):
            self.f.add_provider_record(s["snapshot_id"], a.artifact_sha256, r["record_locator"])

    def test_native_ids_repeat_without_dedup(self):
        provider = self.provider()
        for index in range(2):
            s, a = self.attached(self.snapshot(provider))
            for locator in ['/0', '/1']:
                self.f.add_provider_record(s["snapshot_id"], a.artifact_sha256, locator, provider_native_id="same")
        self.assertEqual(self.db.execute("SELECT count(*) FROM provider_record WHERE provider_native_id='same'").fetchone()[0], 4)

    def test_audit_event_creation(self):
        e = self.f.append_audit_event("created", "fixture", "synthetic", "SYSTEM")
        self.assertEqual(self.f.list_audit_events(entity_type="fixture", entity_id="synthetic"), [e])

    def test_audit_update_delete_rejected(self):
        self.f.append_audit_event("created", "fixture", "synthetic", "MANUAL")
        self.denied("UPDATE audit_event SET note='changed'")
        self.denied("DELETE FROM audit_event")

    def test_audit_supersedes_reference(self):
        e = self.f.append_audit_event("first", "fixture", "synthetic", "SYSTEM")
        later = self.f.append_audit_event("correction", "fixture", "synthetic", "IMPORTED", supersedes_event_id=e["event_id"])
        self.assertEqual(later["supersedes_event_id"], e["event_id"])
        with self.assertRaises(ValidationError):
            self.f.append_audit_event("bad", "fixture", "synthetic", "SYSTEM", supersedes_event_id="missing")

    def test_project_provider_immutable(self):
        self.f.create_project("p", "Project")
        self.provider()
        for table, column in [('review_project','display_name'), ('provider','display_name')]:
            self.denied(f"UPDATE {table} SET {column}='changed'")
            self.denied(f"DELETE FROM {table}")

    def test_replace_cannot_bypass_immutability(self):
        p = self.f.create_project("project", "Project")
        self.denied("INSERT OR REPLACE INTO review_project VALUES (?, 'project', 'Changed', ?)",
                    (p["project_id"], p["created_at_utc"]))

    def test_replace_guard_without_recursive_triggers(self):
        p = self.f.create_project("project", "Project")
        with closing(sqlite3.connect(self.db.path, isolation_level=None)) as raw:
            raw.execute("PRAGMA recursive_triggers=OFF")
            with self.assertRaises(sqlite3.IntegrityError):
                raw.execute("INSERT OR REPLACE INTO review_project VALUES (?, 'project', 'Changed', ?)",
                            (p["project_id"], p["created_at_utc"]))
        self.assertEqual(self.f.get_project(p["project_id"])["display_name"], "Project")

    def test_seal_rolls_back_with_caller_transaction(self):
        s, a = self.attached()
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.f.seal_provider_snapshot(s["snapshot_id"])
                raise RuntimeError("synthetic transaction failure")
        self.assertIsNone(self.f.get_provider_snapshot(s["snapshot_id"])["sealed_at_utc"])
        self.assertIsNone(self.db._sealing_snapshot)

    def test_open_snapshot_verification_refused(self):
        s = self.snapshot()
        with self.assertRaises(ValidationError):
            self.f.verify_provider_snapshot(s["snapshot_id"])

    def test_sensitive_url_values_not_persisted(self):
        # These are synthetic sentinels, never credentials.
        url = 'https://example.test/data?keep=hello+world&' + '&'.join(
            key.upper() + '=synthetic-secret-' + str(i) for i, key in enumerate(sorted(SENSITIVE_QUERY_KEYS)))
        s = self.snapshot(source_locator=url)
        a = self.f.artifacts.put_bytes(b"synthetic")
        self.f.register_raw_artifact(a)
        link = self.f.attach_artifact_to_snapshot(s["snapshot_id"], a.artifact_sha256, "page", 0, source_locator=url)
        for value in [s["source_locator"], link["source_locator"], self.f.get_provider_snapshot(s["snapshot_id"])["source_locator"]]:
            self.assertNotIn("synthetic-secret-", value)
            self.assertIn("keep=hello+world", value)
            self.assertIn("REDACTED", value)

    def test_userinfo_not_persisted(self):
        s = self.snapshot(source_locator="https://synthetic-user:synthetic-password@example.test:443/a?x=1")
        self.assertEqual(s["source_locator"], "https://example.test:443/a?x=1")

    def test_protocol_relative_and_encoded_query_credentials(self):
        url = "//synthetic-user:synthetic-password@example.test/data?%41PI_KEY=synthetic-value&ok=1"
        safe = sanitize_locator(url)
        self.assertEqual(safe, "//example.test/data?API_KEY=REDACTED&ok=1")
        self.assertEqual(sanitize_locator(safe), safe)

    def test_database_rejects_unsanitized_locator_directly(self):
        s = self.snapshot()
        self.denied("INSERT INTO provider_snapshot SELECT 'snap_unsafe',provider_id,dataset,acquisition_mode,"
            "upstream_release,'https://example.test/?key=synthetic-value',retrieved_at_utc,request_spec_sha256,"
            "mirror_commit_sha,parser_name,parser_version,schema_profile,schema_version,license_note,provenance_note,"
            "metadata_json,NULL,NULL FROM provider_snapshot WHERE snapshot_id=?", (s["snapshot_id"],))

    def test_error_does_not_echo_secret(self):
        secret = "synthetic-sensitive-value"
        with self.assertRaises(ValidationError) as caught:
            sanitize_locator('https://['+secret+'/?token='+secret)
        self.assertNotIn(secret, str(caught.exception))
        with self.assertRaises(ValidationError) as caught:
            self.snapshot(metadata_json='{"token":"'+secret+'", INVALID}')
        self.assertNotIn(secret, str(caught.exception))

    def test_json_fields_invalid_through_service(self):
        p = self.provider()
        with self.assertRaises(ValidationError):
            self.snapshot(p, metadata_json="NaN")
        s, a = self.attached(self.snapshot(p))
        for kw in ['request_metadata_json','response_metadata_json']:
            with self.subTest(field=kw), self.assertRaises(ValidationError):
                self.f.attach_artifact_to_snapshot(s["snapshot_id"], a.artifact_sha256, "bad", 1, **{kw: "{"})
        with self.assertRaises(ValidationError):
            self.f.add_provider_record(s["snapshot_id"], a.artifact_sha256, "/bad", metadata_json="{")
        with self.assertRaises(ValidationError):
            self.f.append_audit_event("test", "x", "x", "SYSTEM", payload_json="{")

    def test_json_invalid_direct_sql(self):
        self.snapshot()
        self.denied("INSERT INTO audit_event VALUES ('evt_bad','x','x','x','SYSTEM',NULL,NULL,NULL,'{','2026-01-01T00:00:00Z',NULL)")

    def test_canonical_json_and_non_url_locator(self):
        self.assertEqual(canonical_json({"z": "Việt", "a": 1}), '{"a":1,"z":"Việt"}')
        self.assertEqual(sanitize_locator("local/archive.csv#row=4"), "local/archive.csv#row=4")
        self.assertEqual(sanitize_locator("opaque token=synthetic"), "opaque token=REDACTED")
        with self.assertRaises(ValidationError):
            canonical_json({"x": float("nan")})

    def test_service_immutability_error(self):
        self.provider()
        with self.assertRaises(ImmutabilityError):
            self.db.execute("UPDATE provider SET display_name='changed'")


if __name__ == '__main__':
    unittest.main()

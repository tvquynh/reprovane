"""Forward-only migrations, exact-byte checksums and transactional DDL."""
import hashlib
import re
import sqlite3
from pathlib import Path

from . import MigrationDriftError, MigrationError
from .ids import utc_now

MIGRATION_DIR = Path(__file__).with_name("migrations")
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


def statements(script: str):
    """sqlite3.complete_statement understands semicolons within trigger bodies/strings."""
    pending = ""
    for char in script:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    # Permit trailing comments/whitespace, but reject unterminated executable SQL.
    tail = re.sub(r"/\*.*?\*/|--[^\n]*", "", pending, flags=re.S).strip()
    if tail:
        raise MigrationError("Incomplete migration SQL")


def apply_migrations(connection: sqlite3.Connection, directory: Path = MIGRATION_DIR, *,
                     target_version: int | None = None) -> None:
    if connection.in_transaction:
        raise MigrationError("Migrations require an idle connection")
    try:
        paths = sorted(Path(directory).glob("*.sql"))
        migrations = []
        for path in paths:
            match = _NAME.fullmatch(path.name)
            if not match:
                raise MigrationError("Invalid migration filename")
            data = path.read_bytes()
            migrations.append((int(match[1]), path.name, hashlib.sha256(data).hexdigest(), data.decode("utf-8")))
        versions = [m[0] for m in migrations]
        if not versions or versions != list(range(1, len(versions) + 1)):
            raise MigrationError("Migration versions must be contiguous starting at one")
        if target_version is not None and (type(target_version) is not int or target_version not in versions):
            raise MigrationError("Requested migration target is unavailable")
        # Serialize concurrent initializers and validate the complete history BEFORE applying new DDL.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE TABLE IF NOT EXISTS schema_migration (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,
            applied_at_utc TEXT NOT NULL)""")
        applied = {row[0]: (row[1], row[2]) for row in connection.execute(
            "SELECT version, name, sha256 FROM schema_migration")}
        if sorted(applied) != list(range(1, len(applied) + 1)) or any(v not in versions for v in applied):
            raise MigrationDriftError("Migration history is missing or incompatible")
        for version, name, digest, _ in migrations:
            if version in applied and applied[version] != (name, digest):
                raise MigrationDriftError("Applied migration checksum or name drift detected")
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current != max(applied, default=0):
            raise MigrationDriftError("user_version disagrees with migration ledger")
        for version, name, digest, script in migrations:
            if version in applied or (target_version is not None and version > target_version):
                continue
            for statement in statements(script):
                # Transaction-control/PRAGMA statements would undermine migration atomicity.
                clean = re.sub(r"/\*.*?\*/|--[^\n]*", "", statement, flags=re.S).lstrip()
                if re.match(r"(?i)(BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE|PRAGMA|VACUUM|ATTACH|DETACH)\b", clean):
                    raise MigrationError("Migration contains forbidden transaction or connection control")
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migration VALUES (?, ?, ?, ?)",
                               (version, name, digest, utc_now()))
            connection.execute(f"PRAGMA user_version = {version}")
        connection.execute("COMMIT")
    except Exception as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError("Migration failed; database changes rolled back") from None

"""Explicit SQLite durability/transaction boundary; one connection per Database."""
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import FoundationError, ImmutabilityError, SnapshotSealedError, ValidationError
from .migrations import MIGRATION_DIR, apply_migrations
from .provenance import sanitize_locator


class Database:
    def __init__(self, workspace: Path, *, migration_dir: Path = MIGRATION_DIR, target_schema_version: int = 1):
        self.workspace = Path(workspace).resolve()
        folder = self.workspace / "db"
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.resolve().is_relative_to(self.workspace):
            raise ValidationError("Database directory escapes workspace")
        self.path = folder / "litintel.sqlite"
        if not self.path.resolve().is_relative_to(self.workspace):
            raise ValidationError("Database path escapes workspace")
        self._sealing_snapshot: str | None = None
        self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function("foundation_seal_authorized", 1,
                                        lambda sid: int(sid == self._sealing_snapshot))
        self.connection.create_function("foundation_sha256_text", 1,
                                        lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest(), deterministic=True)
        self.connection.create_function("foundation_sanitize_locator", 1, sanitize_locator, deterministic=True)
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            mode = self.connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            self.connection.execute("PRAGMA synchronous = FULL")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            # REPLACE must fire delete guards too; otherwise it could bypass immutable-row triggers.
            self.connection.execute("PRAGMA recursive_triggers = ON")
            if mode.lower() != "wal" or self.connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise FoundationError("Required SQLite durability settings unavailable")
            apply_migrations(self.connection, migration_dir, target_version=target_schema_version)
        except Exception:
            self.connection.close()
            raise

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        try:
            return self.connection.execute(sql, parameters)
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            if "snapshot sealed" in message:
                raise SnapshotSealedError("Snapshot is sealed") from None
            if "immutable" in message or "seal transition" in message:
                raise ImmutabilityError("Immutable object or unauthorized seal transition") from None
            raise ValidationError("Database constraint rejected the operation") from None
        except sqlite3.DatabaseError:
            raise FoundationError("Database operation failed") from None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self.connection.in_transaction:
            raise FoundationError("Nested transactions are unsupported; reuse the active transaction")
        self.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

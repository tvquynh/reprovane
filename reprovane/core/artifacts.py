"""Immutable exact-byte SHA-256 objects. Placement precedes database registration."""
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from . import ArtifactIntegrityError, ValidationError


@dataclass(frozen=True)
class StoredArtifact:
    artifact_sha256: str
    byte_size: int
    relative_path: str


class ContentAddressedArtifactStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / "raw" / "sha256"

    def path_for_hash(self, digest: str) -> Path:
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValidationError("Invalid artifact SHA-256")
        path = self.root / digest[:2] / digest
        if not path.resolve().is_relative_to(self.workspace):
            raise ValidationError("Artifact path escapes workspace")
        return path

    def verify(self, digest: str) -> StoredArtifact:
        path = self.path_for_hash(digest)
        try:
            hasher = hashlib.sha256()
            size = 0
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
                    size += len(chunk)
            if hasher.hexdigest() != digest:
                raise ArtifactIntegrityError("Artifact hash mismatch")
            return StoredArtifact(digest, size, path.relative_to(self.workspace).as_posix())
        except OSError:
            raise ArtifactIntegrityError("Artifact missing or unreadable") from None

    @staticmethod
    def _sync_directory(path: Path) -> None:
        # Windows does not expose a portable directory fsync; file fsync is still mandatory.
        if os.name == "posix":
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _put_stream(self, stream: BinaryIO) -> StoredArtifact:
        # Staging shares the destination filesystem. No user locator enters a filename.
        if not self.root.resolve().is_relative_to(self.workspace):
            raise ValidationError("Artifact directory escapes workspace")
        self.root.mkdir(parents=True, exist_ok=True)
        tmp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".put-", delete=False) as handle:
                tmp = Path(handle.name)
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            key = digest.hexdigest()
            target = self.path_for_hash(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                return self.verify(key)
            os.replace(tmp, target)
            # Persist the new file entry AND newly created shard/root directory entries.
            for folder in (target.parent, self.root, self.root.parent, self.workspace):
                self._sync_directory(folder)
            return self.verify(key)
        except OSError:
            raise ArtifactIntegrityError("Artifact placement failed") from None
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def put_bytes(self, data: bytes) -> StoredArtifact:
        from io import BytesIO
        if not isinstance(data, bytes):
            raise ValidationError("Artifact input must be bytes")
        return self._put_stream(BytesIO(data))

    def put_file(self, path: Path) -> StoredArtifact:
        try:
            with Path(path).open("rb") as handle:
                return self._put_stream(handle)
        except OSError:
            raise ArtifactIntegrityError("Artifact source missing or unreadable") from None

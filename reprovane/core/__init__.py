"""Isolated standard-library persistence foundation; unused by the legacy CLI."""


class FoundationError(Exception):
    """Base error for foundation operations (messages contain no input values)."""


class MigrationError(FoundationError):
    pass


class MigrationDriftError(MigrationError):
    pass


class ImmutabilityError(FoundationError):
    pass


class SnapshotSealedError(ImmutabilityError):
    pass


class ArtifactIntegrityError(FoundationError):
    pass


class ValidationError(FoundationError):
    pass

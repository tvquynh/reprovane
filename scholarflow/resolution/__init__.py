"""Conservative bibliographic publication resolution over immutable typed claims."""
from ..core import FoundationError


class ResolutionError(FoundationError):
    pass


class ResolutionStateError(ResolutionError):
    pass


class CandidateBlockTooLargeError(ResolutionError):
    def __init__(self, method: str, block_size: int, configured_limit: int):
        self.method, self.block_size, self.configured_limit = method, block_size, configured_limit
        super().__init__(f"Candidate block too large: method={method}, block_size={block_size}, configured_limit={configured_limit}")


class ResolutionConflictError(ResolutionError):
    pass


class EvidenceAmbiguityError(ResolutionError):
    pass


class DecisionSupersessionError(ResolutionError):
    pass


from .store import ResolutionStore  # noqa: E402 - errors must exist before store imports them

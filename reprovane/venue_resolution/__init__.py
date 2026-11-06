"""Venue identity remains separate from provider membership/ranking claims."""
from ..resolution import (ResolutionError, ResolutionStateError, ResolutionConflictError,
                          DecisionSupersessionError, CandidateBlockTooLargeError)


class VenueCandidateBlockTooLargeError(CandidateBlockTooLargeError):
    pass


from .store import VenueResolutionStore

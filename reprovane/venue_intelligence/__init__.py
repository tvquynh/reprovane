"""Explicit Scopus/WoS facts over the existing venue resolution architecture."""
from .store import VenueIntelligenceStore,import_venue_lists,sync_venue_lists,DEFAULT_FILES,SCOPUS_PATH,COLLECTIONS,PROFILES
from .projection import VenueIntelligence,resolve_imported_snapshots

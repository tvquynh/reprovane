"""Venue tiers from a list the user maintains (one row per venue).

The list is the user's policy, not data: the tool never decides which venues are good. A paper gets the
tier of the listed venue that its source matches, by OpenAlex source ID first, then by ISSN, then by the
whole OpenAlex source name, then by a phrase contained in that name (the longest matching phrase wins).
A paper whose source is not listed gets UNLISTED; one that matches two listed venues at the deciding step
gets AMBIGUOUS, with both venue names, so the list can be corrected instead of a venue being picked silently.
"""
from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .common import normalize_issn, safe_str, short_openalex_id, split_issns

COLUMNS = ("tier", "venue", "openalex_source_id", "issn", "title_exact", "title_contains")
KEY_COLUMNS = ("openalex_source_id", "issn", "title_exact", "title_contains")
RESERVED_TIERS = {"UNLISTED", "AMBIGUOUS"}
TIER_FIELDS = ("venue_tier", "venue_tier_venue", "venue_tier_match")

_VALID_ISSN_RE = re.compile(r"^\d{7}[\dX]$")
_SOURCE_ID_RE = re.compile(r"^S\d+$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


class VenueTierError(ValueError):
    """The venue list cannot be used; the message names the line to fix."""


def phrase_key(value: Any) -> str:
    """Lower-case words separated by one space; '&' counts as 'and' and accents are dropped."""
    text = safe_str(value).replace("&", " and ")
    if not text.isascii():
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(_NON_ALNUM_RE.sub(" ", text.casefold()).split())


@dataclass(frozen=True)
class Venue:
    tier: str
    name: str
    line: int


def _cells(value: str) -> List[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


def _distinct(venues: Iterable[Venue]) -> List[Venue]:
    return sorted(set(venues), key=lambda v: v.line)


class VenueTiers:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.sha256 = ""
        self.venues: List[Venue] = []
        self.by_source_id: Dict[str, Venue] = {}
        self.by_issn: Dict[str, Venue] = {}
        self.by_title: Dict[str, Venue] = {}
        self.by_phrase: Dict[str, Venue] = {}

    @classmethod
    def load(cls, path: Path) -> "VenueTiers":
        tiers = cls(path)
        try:
            data = tiers.path.read_bytes()
            text = data.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise VenueTierError(f"cannot read the venue list {path}: {exc}") from None
        # Hash of the bytes the rules were built from, so later edits to the file cannot mislabel a run.
        tiers.sha256 = hashlib.sha256(data).hexdigest()
        delimiter = "," if tiers.path.suffix.lower() == ".csv" else "\t"
        numbered = [(number, line) for number, line in enumerate(text.splitlines(), start=1)
                    if line.strip() and not line.lstrip().startswith("#")]
        if not numbered:
            raise VenueTierError(f"{path}: no header row")
        header_number, header_line = numbered[0]
        header = [cell.strip().casefold() for cell in next(csv.reader([header_line], delimiter=delimiter))]
        unknown = [name for name in header if name not in COLUMNS]
        if unknown or len(set(header)) != len(header):
            raise VenueTierError(
                f"{path} line {header_number}: the header must use each of these columns at most once: "
                f"{', '.join(COLUMNS)} (found {', '.join(header)})"
            )
        if "tier" not in header or "venue" not in header or not set(KEY_COLUMNS) & set(header):
            raise VenueTierError(f"{path} line {header_number}: the header needs tier, venue and at least one of "
                                 f"{', '.join(KEY_COLUMNS)}")
        for number, line in numbered[1:]:
            cells = next(csv.reader([line], delimiter=delimiter))
            if len(cells) > len(header) and any(cell.strip() for cell in cells[len(header):]):
                raise VenueTierError(f"{path} line {number}: more cells than header columns")
            row = {name: (cells[i].strip() if i < len(cells) else "") for i, name in enumerate(header)}
            tiers._add(row, number)
        if not tiers.venues:
            raise VenueTierError(f"{path}: the list has a header but no venues")
        return tiers

    def _add(self, row: Dict[str, str], number: int) -> None:
        where = f"{self.path} line {number}"
        tier, name = row.get("tier", ""), row.get("venue", "")
        if not tier or not name:
            raise VenueTierError(f"{where}: tier and venue must not be empty")
        if tier.upper() in RESERVED_TIERS:
            raise VenueTierError(f"{where}: {tier} is reserved for papers the list does not decide")
        venue = Venue(tier, name, number)
        keys: List[Tuple[Dict[str, Venue], str, str]] = []
        for value in _cells(row.get("openalex_source_id", "")):
            source_id = short_openalex_id(value).upper()
            if not _SOURCE_ID_RE.match(source_id):
                raise VenueTierError(f"{where}: {value!r} is not an OpenAlex source ID (S followed by digits)")
            keys.append((self.by_source_id, source_id, f"OpenAlex source {source_id}"))
        for value in _cells(row.get("issn", "")):
            issn = normalize_issn(value)
            if not _VALID_ISSN_RE.match(issn):
                raise VenueTierError(f"{where}: {value!r} is not a valid ISSN")
            keys.append((self.by_issn, issn, f"ISSN {issn[:4]}-{issn[4:]}"))
        for column, index, label in (("title_exact", self.by_title, "source name"),
                                     ("title_contains", self.by_phrase, "phrase")):
            for value in _cells(row.get(column, "")):
                phrase = phrase_key(value)
                if not phrase:
                    raise VenueTierError(f"{where}: {column} {value!r} has no letters or digits")
                keys.append((index, phrase, f"{label} {phrase!r}"))
        if not keys:
            raise VenueTierError(f"{where}: give an openalex_source_id, issn, title_exact or title_contains for {name}")
        for index, key, label in keys:
            if key in index:
                other = index[key]
                raise VenueTierError(f"{where}: {label} is already listed for {other.name} (line {other.line})")
            index[key] = venue
        self.venues.append(venue)

    def match(self, row: Dict[str, Any]) -> Tuple[str, str, str]:
        """Return (tier, venue, match step) for one paper; ('UNLISTED', '', '') when nothing matches."""
        source_id = short_openalex_id(safe_str(row.get("source_openalex_id"))).upper()
        if source_id in self.by_source_id:
            return self._decide([self.by_source_id[source_id]], "openalex_source_id")
        issns = split_issns(safe_str(row.get("issn_l"))) + split_issns(safe_str(row.get("issn")))
        hits = _distinct(self.by_issn[issn] for issn in issns if issn in self.by_issn)
        if hits:
            return self._decide(hits, "issn")
        name = phrase_key(row.get("source"))
        if name in self.by_title:
            return self._decide([self.by_title[name]], "title_exact")
        if name:
            padded = f" {name} "
            found = [(len(phrase), venue) for phrase, venue in self.by_phrase.items() if f" {phrase} " in padded]
            if found:
                longest = max(length for length, _ in found)
                return self._decide(_distinct(v for length, v in found if length == longest), "title_contains")
        return "UNLISTED", "", ""

    @staticmethod
    def _decide(hits: List[Venue], step: str) -> Tuple[str, str, str]:
        if len(hits) == 1:
            return hits[0].tier, hits[0].name, step
        return "AMBIGUOUS", " | ".join(v.name for v in hits), step

    def assign(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            row["venue_tier"], row["venue_tier_venue"], row["venue_tier_match"] = self.match(row)

    def describe(self) -> Dict[str, Any]:
        return {
            "file": str(self.path),
            "sha256": self.sha256,
            "venues": len(self.venues),
            "tiers": sorted({venue.tier for venue in self.venues}),
        }


def admit_tier_problem(admit_tiers: Iterable[str], tiers: Optional[VenueTiers]) -> Optional[str]:
    """Why the tiers given to --admit-tier cannot be used with this venue list, or None."""
    wanted = [tier for tier in admit_tiers if tier]
    if not wanted:
        return None
    if tiers is None:
        return "--admit-tier needs a venue list (--venue-tiers, or screening.venue_tiers in config.json)"
    reserved = sorted({tier for tier in wanted if tier.upper() in RESERVED_TIERS})
    if reserved:
        return f"{', '.join(reserved)} cannot be admitted: those papers are not decided by the list"
    known = {venue.tier for venue in tiers.venues}
    missing = sorted(set(wanted) - known)
    if missing:
        return f"tier {', '.join(missing)} is not in {tiers.path} (its tiers: {', '.join(sorted(known))})"
    return None

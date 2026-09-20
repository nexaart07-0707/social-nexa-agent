"""
Module 2 — Business Discovery

Owns: turning (city, niches, radius) into a deduped candidate list of
{name, handle_guess, address, lat, lon, niche, chain_group_id | null}.
Does NOT own: any Instagram/instaloader interaction. `handle_guess` here is
only a best-effort slug derived from the business name — Module 3
(collector.py) is responsible for actually resolving/verifying a real
Instagram handle against Instagram itself. Nothing in this module ever
filters on follower count (that is Module 4's scoring concern).

Data source: OpenStreetMap Nominatim (https://nominatim.openstreetmap.org/
search), a free, public, rate-limited API. Per Nominatim's usage policy this
module:
  - sends a descriptive User-Agent identifying this as a non-commercial
    research/lead-discovery tool (Nominatim requires this, and blocks
    generic/default User-Agents),
  - sleeps at least 1 second between requests (module-level MIN_REQUEST_
    INTERVAL_SECONDS), enforced by a tiny rate limiter,
  - issues a small, bounded number of requests per run (one per
    city x niche combination) rather than a large or unbounded sweep.

"Nationwide India search": Nominatim has no single query that means
"anywhere in India" beyond the country-level `India` area, and running an
unbounded, unstructured query against a free rate-limited API is not
practical (it would return an essentially arbitrary slice of the country,
not a representative nationwide sweep). So "nationwide" is implemented here
as querying a small FIXED list of major Indian cities/metros spanning
multiple states (see NATIONWIDE_CITIES below) — a practical, documented
stand-in for "search all of India" that this project's free-tier constraint
requires.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim usage policy requires a descriptive User-Agent identifying the
# application (not a browser UA, not the default python-requests one).
USER_AGENT = "SocialNexaAgent-BusinessDiscovery/1.0 (research/lead-gen tool)"

# Nominatim's usage policy caps free usage at ~1 request/second. We enforce a
# minimum gap between any two outgoing requests from this module.
MIN_REQUEST_INTERVAL_SECONDS = 1.0

# Two candidates within this distance are considered "the same physical
# location" for dedupe purposes (in addition to a fuzzy name match).
DEDUPE_RADIUS_METERS = 100.0

# The Run-1 required local city (PRD.md §5).
HUBLI = "Hubli, Karnataka, India"

# Fixed stand-in for "nationwide India search" (see module docstring). A
# spread of major metros across different states/regions, so a nationwide
# run isn't biased toward one region.
#
# ponytail: kept to 6 cities (was 20) after a live GitHub Actions run showed
# the unconditional city x niche x tag sweep at >=1 req/s to Nominatim is a
# ~7min fixed cost regardless of any candidate cap downstream -- cut here so
# a full run finishes in a reasonable window. Upgrade path: rotate which 6
# cities are used per run (e.g. by day-of-week) if broader nationwide
# coverage over time is wanted without paying the full 20-city cost per run.
NATIONWIDE_CITIES: list[str] = [
    "Mumbai, Maharashtra, India",
    "Delhi, India",
    "Bengaluru, Karnataka, India",
    "Chennai, Tamil Nadu, India",
    "Kolkata, West Bengal, India",
    "Ahmedabad, Gujarat, India",
]

# Per-niche OSM tag mapping. Each niche maps to a list of (osm_key, osm_value)
# pairs; Nominatim's `amenity=`/`shop=` style free-text query is built as
# "<value> in <city>" using the OSM tag vocabulary as the search keyword,
# since Nominatim's plain /search endpoint is a geocoder (name/address
# search), not a structured Overpass-style tag query. This keeps Module 2
# using only the Nominatim `search` endpoint (per TRD.md), while still
# steering results toward the right category via the query text derived
# from real OSM tag values.
NICHE_OSM_TAGS: dict[str, list[tuple[str, str]]] = {
    "cafes/bakeries/home-food": [
        ("amenity", "cafe"),
        ("shop", "bakery"),
        ("shop", "pastry"),
    ],
    "tutors/coaching": [
        ("office", "educational_institution"),
        ("amenity", "language_school"),
    ],
    "restaurants": [
        ("amenity", "restaurant"),
        ("amenity", "fast_food"),
    ],
    "salons/spas/beauty": [
        ("shop", "hairdresser"),
        ("shop", "beauty"),
        ("leisure", "spa"),
    ],
    "boutiques/clothing/jewelry": [
        ("shop", "boutique"),
        ("shop", "clothes"),
        ("shop", "jewelry"),
    ],
    "clinics/doctors/dentists/vets/pet-groomers": [
        ("amenity", "clinic"),
        ("amenity", "dentist"),
        ("amenity", "veterinary"),
        ("shop", "pet_grooming"),
    ],
}

RunName = Literal["run1", "run2"]


@dataclass
class Candidate:
    name: str
    handle_guess: str
    address: str
    lat: float
    lon: float
    niche: str
    chain_group_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "handle_guess": self.handle_guess,
            "address": self.address,
            "lat": self.lat,
            "lon": self.lon,
            "niche": self.niche,
            "chain_group_id": self.chain_group_id,
        }


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Enforces a minimum gap between successive Nominatim requests."""

    def __init__(self, min_interval: float = MIN_REQUEST_INTERVAL_SECONDS):
        self.min_interval = min_interval
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            elapsed = time.monotonic() - self._last_call
            remaining = self.min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()


_rate_limiter = _RateLimiter()


# ---------------------------------------------------------------------------
# Nominatim query
# ---------------------------------------------------------------------------


def _slugify_handle(name: str) -> str:
    """Best-effort Instagram handle guess from a business name.

    This is only a starting guess for Module 3 to verify against real
    Instagram accounts — it is NOT a confirmed handle.
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    slug = re.sub(r"[^a-z0-9]+", "", lowered)
    return slug[:30] if slug else "unknown"


def _query_nominatim(query: str, session: requests.Session | None = None) -> list[dict]:
    """Issue one rate-limited request to Nominatim's /search endpoint.

    Wrapped in try/except per Architecture.md's failure-isolation rule: a
    single failed request never crashes the whole discovery run, it just
    contributes zero candidates for that (city, niche) combination.
    """
    http = session or requests
    _rate_limiter.wait()
    try:
        resp = http.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 20,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except (requests.RequestException, ValueError) as exc:
        print(f"[discovery] Nominatim request failed for query={query!r}: {exc}")
        return []


def search_city_niche(
    city: str,
    niche: str,
    session: requests.Session | None = None,
) -> list[Candidate]:
    """Query Nominatim for one niche's OSM tag values within one city.

    `radius_km` isn't needed here for the Hubli case (PRD.md §5 says Run 1's
    radius is simply "within Hubli city limits", i.e. the city-level
    Nominatim query itself), so this function takes a place name and relies
    on Nominatim's own city-scoping. A dedicated radius filter is not
    implemented since nothing in scope currently needs a sub-city radius.
    """
    tag_values = NICHE_OSM_TAGS.get(niche, [])
    candidates: list[Candidate] = []
    for _osm_key, osm_value in tag_values:
        keyword = osm_value.replace("_", " ")
        query = f"{keyword} in {city}"
        results = _query_nominatim(query, session=session)
        for item in results:
            try:
                name = item.get("name") or item.get("display_name", "").split(",")[0]
                if not name:
                    continue
                candidates.append(
                    Candidate(
                        name=name,
                        handle_guess=_slugify_handle(name),
                        address=item.get("display_name", ""),
                        lat=float(item["lat"]),
                        lon=float(item["lon"]),
                        niche=niche,
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                print(f"[discovery] Skipping malformed Nominatim item {item!r}: {exc}")
    return candidates


def search_city(
    city: str,
    niches: list[str],
    session: requests.Session | None = None,
) -> list[Candidate]:
    """Query all given niches within one city."""
    candidates: list[Candidate] = []
    for niche in niches:
        candidates.extend(search_city_niche(city, niche, session=session))
    return candidates


def search_nationwide(
    niches: list[str],
    cities: list[str] | None = None,
    session: requests.Session | None = None,
) -> list[Candidate]:
    """"Nationwide" search implemented as a sweep over NATIONWIDE_CITIES.

    See module docstring for why a fixed city list stands in for a true
    unbounded nationwide query.
    """
    cities = cities if cities is not None else NATIONWIDE_CITIES
    candidates: list[Candidate] = []
    for city in cities:
        candidates.extend(search_city(city, niches, session=session))
    return candidates


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def _normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    # Drop common generic/legal suffixes and punctuation so branches of the
    # same chain ("Cafe Coffee Day - Hubli", "Cafe Coffee Day Pvt Ltd")
    # normalize to the same key.
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    lowered = re.sub(
        r"\b(pvt|ltd|private|limited|the|branch|outlet)\b", " ", lowered
    )
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    r = 6_371_000.0  # Earth radius in meters
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * r * asin(sqrt(a))


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Dedupe by normalized-name + geo-proximity.

    - Two entries with the same normalized name AND within
      DEDUPE_RADIUS_METERS of each other are treated as the exact same
      physical business and merged into one entry.
    - Two entries with the same normalized name but FARTHER apart are kept
      as separate records (different branches of the same chain) and both
      tagged with a shared `chain_group_id`.
    - Entries with a unique normalized name are left untouched.
    """
    groups: dict[str, list[Candidate]] = {}
    for c in candidates:
        groups.setdefault(_normalize_name(c.name), []).append(c)

    result: list[Candidate] = []
    for norm_name, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
            continue

        # Merge near-duplicates (same name, same physical spot) within the
        # group first.
        merged: list[Candidate] = []
        for cand in group:
            placed = False
            for kept in merged:
                if (
                    _haversine_meters(cand.lat, cand.lon, kept.lat, kept.lon)
                    <= DEDUPE_RADIUS_METERS
                ):
                    placed = True
                    break
            if not placed:
                merged.append(cand)

        if len(merged) == 1:
            result.append(merged[0])
        else:
            # Genuinely distinct branches of the same-named business: keep
            # all, tag with a shared chain_group_id.
            chain_id = f"chain:{norm_name}"
            for cand in merged:
                cand.chain_group_id = chain_id
                result.append(cand)

    return result


# ---------------------------------------------------------------------------
# Top-level run logic (PRD.md §5)
# ---------------------------------------------------------------------------


def discover_for_run(
    run: RunName,
    niches: list[str],
    max_results: int | None = None,
    session: requests.Session | None = None,
) -> list[dict]:
    """Compose the pieces above per PRD.md's Run 1 / Run 2 discovery rules.

    run1: Hubli (city-limits query) + nationwide fill, per PRD.md §5's
          "1-2 qualifying results MUST be from Hubli ... remaining results
          ... from a true nationwide search" requirement. This function only
          gathers candidates (it does not itself enforce the 1-2-from-Hubli
          *qualifying* count — that requires scores, which is Module 4's
          job; this module just makes sure Hubli is represented in the pool
          alongside the nationwide pool).
    run2: purely nationwide, no location privileging at all.
    """
    if run == "run1":
        hubli_candidates = search_city(HUBLI, niches, session=session)
        nationwide_candidates = search_nationwide(niches, session=session)
        all_candidates = hubli_candidates + nationwide_candidates
    elif run == "run2":
        all_candidates = search_nationwide(niches, session=session)
    else:
        raise ValueError(f"Unknown run: {run!r}")

    deduped = dedupe_candidates(all_candidates)
    result = [c.as_dict() for c in deduped]
    if max_results is not None:
        result = result[:max_results]
    return result

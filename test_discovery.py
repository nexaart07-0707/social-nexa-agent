"""
Tests for Module 2 — Business Discovery (discovery.py).

All Nominatim HTTP calls are mocked (unittest.mock) — no real network calls
happen in this test file. A separate, manual live check against the real
Nominatim API was run during development for the DoD sign-off (see the
worker report); it is intentionally not part of the automated pytest suite
so CI/local test runs never depend on network availability or Nominatim's
rate limits.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import discovery


def _item(name, lat, lon, display_name=None):
    return {
        "name": name,
        "display_name": display_name or f"{name}, Some Street, Hubli, Karnataka, India",
        "lat": str(lat),
        "lon": str(lon),
    }


# ---------------------------------------------------------------------------
# Dedupe tests
# ---------------------------------------------------------------------------


def test_dedupe_merges_near_identical_entries():
    c1 = discovery.Candidate(
        name="Cafe Coffee Day",
        handle_guess="cafecoffeeday",
        address="123 Main St, Hubli",
        lat=15.3647,
        lon=75.1240,
        niche="cafes/bakeries/home-food",
    )
    # Same business, tiny coordinate jitter (~a few meters), slightly
    # different name casing/punctuation -> should merge into one.
    c2 = discovery.Candidate(
        name="Cafe Coffee Day",
        handle_guess="cafecoffeeday",
        address="123 Main St, Hubli",
        lat=15.36471,
        lon=75.12401,
        niche="cafes/bakeries/home-food",
    )
    result = discovery.dedupe_candidates([c1, c2])
    assert len(result) == 1
    assert result[0].chain_group_id is None


def test_dedupe_keeps_distinct_chain_branches_with_shared_chain_group_id():
    branch1 = discovery.Candidate(
        name="Cafe Coffee Day",
        handle_guess="cafecoffeeday",
        address="Branch A, Hubli",
        lat=15.3647,
        lon=75.1240,
        niche="cafes/bakeries/home-food",
    )
    # Same normalized name, but far away (different branch, ~ a few km away).
    branch2 = discovery.Candidate(
        name="Cafe Coffee Day",
        handle_guess="cafecoffeeday",
        address="Branch B, Hubli",
        lat=15.4000,
        lon=75.1600,
        niche="cafes/bakeries/home-food",
    )
    result = discovery.dedupe_candidates([branch1, branch2])
    assert len(result) == 2
    ids = {c.chain_group_id for c in result}
    assert len(ids) == 1
    assert None not in ids


def test_dedupe_leaves_unrelated_businesses_untouched():
    a = discovery.Candidate(
        name="Sunrise Bakery",
        handle_guess="sunrisebakery",
        address="Addr A",
        lat=15.30,
        lon=75.10,
        niche="cafes/bakeries/home-food",
    )
    b = discovery.Candidate(
        name="Moonlight Salon",
        handle_guess="moonlightsalon",
        address="Addr B",
        lat=19.07,
        lon=72.87,
        niche="salons/spas/beauty",
    )
    result = discovery.dedupe_candidates([a, b])
    assert len(result) == 2
    assert all(c.chain_group_id is None for c in result)


# ---------------------------------------------------------------------------
# discover_for_run tests (HTTP mocked)
# ---------------------------------------------------------------------------


def _make_mock_session(items_by_query=None):
    """Build a fake requests.Session-like object whose .get() returns a
    canned response depending on the query string, without hitting the
    network."""
    items_by_query = items_by_query or {}

    def fake_get(url, params=None, headers=None, timeout=None):
        query = (params or {}).get("q", "")
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        # default: one generic result so every query returns *something*
        matched = None
        for key, items in items_by_query.items():
            if key in query:
                matched = items
                break
        resp.json.return_value = matched if matched is not None else [_item("Generic Biz", 10.0, 20.0)]
        return resp

    session = MagicMock()
    session.get.side_effect = fake_get
    return session


@patch("discovery.time.sleep", return_value=None)  # skip real rate-limit delays in tests
def test_discover_for_run_run1_queries_hubli_and_nationwide(mock_sleep):
    session = _make_mock_session()
    niches = ["cafes/bakeries/home-food"]

    result = discovery.discover_for_run("run1", niches, session=session)

    assert len(result) > 0

    queried_strings = [
        call.kwargs["params"]["q"] for call in session.get.call_args_list
    ]
    assert any("Hubli" in q for q in queried_strings), "run1 must query Hubli"
    assert any(
        any(city.split(",")[0] in q for city in discovery.NATIONWIDE_CITIES)
        for q in queried_strings
    ), "run1 must also query at least one nationwide city"


@patch("discovery.time.sleep", return_value=None)
def test_discover_for_run_run2_does_not_privilege_hubli(mock_sleep):
    session = _make_mock_session()
    niches = ["restaurants"]

    result = discovery.discover_for_run("run2", niches, session=session)

    assert len(result) > 0

    queried_strings = [
        call.kwargs["params"]["q"] for call in session.get.call_args_list
    ]
    # run2 must not perform a dedicated Hubli-city-limits query - Hubli isn't
    # in NATIONWIDE_CITIES at all, so no query should ever mention it.
    assert not any("Hubli" in q for q in queried_strings)


@patch("discovery.time.sleep", return_value=None)
def test_discover_for_run_respects_max_results(mock_sleep):
    # Make every niche/city query return several distinct candidates so we
    # can verify truncation.
    items = [_item(f"Business {i}", 10.0 + i * 0.01, 20.0 + i * 0.01) for i in range(5)]
    session = _make_mock_session(items_by_query={"restaurant": items, "fast food": items})

    result = discovery.discover_for_run(
        "run2", ["restaurants"], max_results=3, session=session
    )
    assert len(result) <= 3


def test_query_nominatim_handles_request_failure_gracefully():
    session = MagicMock()
    session.get.side_effect = discovery.requests.RequestException("boom")
    result = discovery._query_nominatim("cafe in Hubli, Karnataka, India", session=session)
    assert result == []

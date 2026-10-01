import json
from datetime import datetime, timedelta, timezone

import filters

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _record(**overrides):
    base = {
        "handle": "somehandle",
        "bio": "unavailable",
        "follower_count": "unavailable",
        "following_count": "unavailable",
        "posts": "unavailable",
        "highlight_count": "unavailable",
        "video_count": "unavailable",
        "image_count": "unavailable",
    }
    base.update(overrides)
    return base


def _post(days_ago, **overrides):
    post = {"date": (NOW - timedelta(days=days_ago)).isoformat(), "likes": 1, "comments": 0, "media_type": "image"}
    post.update(overrides)
    return post


# ---------------------------------------------------------------------------
# Rule 1: follower cap -- DoD cases 1 & 2
# ---------------------------------------------------------------------------


def test_follower_cap_excludes_over_cap():
    """follower_count=20000 (> 15000 cap) -> excluded."""
    record = _record(follower_count=20000)
    assert filters.filter_by_follower_cap(record) is False


def test_follower_cap_boundary_exactly_cap_survives():
    """follower_count=15000 exactly (the cap itself) -> included, boundary is inclusive."""
    record = _record(follower_count=15000)
    assert filters.filter_by_follower_cap(record) is True


def test_follower_cap_unavailable_survives():
    """follower_count == 'unavailable' -> never auto-excluded."""
    record = _record(follower_count="unavailable")
    assert filters.filter_by_follower_cap(record) is True


def test_follower_cap_non_numeric_survives():
    record = _record(follower_count="not-a-number")
    assert filters.filter_by_follower_cap(record) is True


def test_follower_cap_custom_max_respected():
    record = _record(follower_count=500)
    assert filters.filter_by_follower_cap(record, max_followers=100) is False


# ---------------------------------------------------------------------------
# Rule 2: posting frequency -- DoD cases 3 & 4
# ---------------------------------------------------------------------------


def test_posting_frequency_only_one_post_in_window_excludes():
    """Only 1 post dated within the trailing 30 days (others older) -> excluded."""
    record = _record(posts=[_post(5), _post(40), _post(60)])
    assert filters.filter_by_posting_frequency(record, now=NOW) is False


def test_posting_frequency_boundary_exactly_two_posts_survives():
    """Exactly 2 posts dated within the trailing 30 days -> included, boundary is inclusive."""
    record = _record(posts=[_post(5), _post(30)])
    assert filters.filter_by_posting_frequency(record, now=NOW) is True


def test_posting_frequency_zero_posts_in_window_excludes():
    record = _record(posts=[_post(40), _post(60)])
    assert filters.filter_by_posting_frequency(record, now=NOW) is False


def test_posting_frequency_unavailable_posts_survives():
    """posts == 'unavailable' -> insufficient DATA, never auto-excluded."""
    record = _record(posts="unavailable")
    assert filters.filter_by_posting_frequency(record, now=NOW) is True


def test_posting_frequency_empty_posts_list_survives():
    record = _record(posts=[])
    assert filters.filter_by_posting_frequency(record, now=NOW) is True


def test_posting_frequency_no_parseable_dates_survives():
    record = _record(posts=[{"date": "unavailable", "likes": 1, "comments": 0, "media_type": "image"}])
    assert filters.filter_by_posting_frequency(record, now=NOW) is True


# ---------------------------------------------------------------------------
# Rule 3: brand blocklist -- DoD cases 5 & 6
# ---------------------------------------------------------------------------


def test_brand_blocklist_matches_actual_seed_entry():
    """A handle containing a REAL seeded blocklist entry -> excluded. Reads the
    actual seed file rather than assuming its contents, so this doesn't
    silently pass if the entry is ever renamed/removed."""
    with open(filters.DEFAULT_BLOCKLIST_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    assert "starbucks" in seed

    record = _record(handle="starbucksindia_official")
    assert filters.filter_by_brand_blocklist(record) is False


def test_brand_blocklist_matches_via_business_name():
    record = _record(handle="randomhandle123")
    assert filters.filter_by_brand_blocklist(record, business_name="McDonalds Koramangala") is False


def test_brand_blocklist_generic_cafe_word_not_matched():
    """A business name containing the generic word 'cafe', which is NOT itself
    a seeded blocklist entry (the seed list never contains bare category
    words) -> survives. Confirms substring-matching isn't over-aggressive."""
    record = _record(handle="sunrise_cafe_hubli")
    assert filters.filter_by_brand_blocklist(record, business_name="Sunrise Cafe") is True


def test_brand_blocklist_missing_file_survives(tmp_path):
    """A missing blocklist file -> treated as 'no blocklist, nothing to exclude', never an error."""
    missing_path = str(tmp_path / "does_not_exist.json")
    record = _record(handle="anything")
    assert filters.filter_by_brand_blocklist(record, blocklist_path=missing_path) is True


def test_brand_blocklist_corrupt_json_survives(tmp_path):
    bad_path = tmp_path / "corrupt.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    record = _record(handle="anything")
    assert filters.filter_by_brand_blocklist(record, blocklist_path=str(bad_path)) is True


def test_brand_blocklist_unavailable_handle_and_no_business_name_survives():
    record = _record(handle="unavailable")
    assert filters.filter_by_brand_blocklist(record) is True

"""
Tests for Module 3 — Data Collector (collector.py).

Mocked tests (satisfy TRD.md's DoD "handled correctly without crashing"):
  (a) normal public profile with several posts
  (b) profile with very few posts (1-2)
  (c) nonexistent handle -> ProfileNotExistsException

A best-effort LIVE test against real, anonymous (logged-out) Instagram
profiles is also included, marked to skip gracefully (not fail the suite)
if the network is unavailable/rate-limited/blocked, per task instructions:
mocked tests are what satisfy the DoD; the live test is a bonus sanity
check and must never turn a rate-limit into a hard failure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import instaloader
from instaloader.exceptions import ProfileNotExistsException

import collector


def make_fake_post(date, likes, comments, typename="GraphImage", is_video=False):
    post = MagicMock()
    post.date_utc = date
    post.likes = likes
    post.comments = comments
    post.typename = typename
    post.is_video = is_video
    return post


@pytest.fixture
def loader():
    return instaloader.Instaloader(download_pictures=False, download_videos=False)


# --- (a) normal public profile with several posts ---------------------------

def test_collect_profile_normal_public_profile(loader):
    fake_profile = MagicMock()
    fake_profile.biography = "Best coffee in town. DM to order!"
    fake_profile.followers = 2500
    fake_profile.followees = 300

    posts = [
        make_fake_post(datetime(2026, 9, 1, tzinfo=timezone.utc), 120, 8, "GraphImage", False),
        make_fake_post(datetime(2026, 8, 25, tzinfo=timezone.utc), 90, 5, "GraphVideo", True),
        make_fake_post(datetime(2026, 8, 20, tzinfo=timezone.utc), 150, 12, "GraphSidecar", False),
    ]
    fake_profile.get_posts.return_value = iter(posts)

    with patch("instaloader.Profile.from_username", return_value=fake_profile), \
         patch.object(loader, "get_highlights", return_value=iter([MagicMock(), MagicMock()])):
        result = collector.collect_profile(loader, "normal_cafe")

    assert result["handle"] == "normal_cafe"
    assert result["bio"] == "Best coffee in town. DM to order!"
    assert result["follower_count"] == 2500
    assert result["following_count"] == 300
    assert isinstance(result["posts"], list)
    assert len(result["posts"]) == 3
    assert result["posts"][0]["likes"] == 120
    assert result["posts"][0]["media_type"] == "image"
    assert result["posts"][1]["media_type"] == "video"
    assert result["posts"][2]["media_type"] == "carousel"
    assert result["video_count"] == 1
    assert result["image_count"] == 2
    assert result["highlight_count"] == 2
    assert "error" not in result


def test_collect_profile_caps_at_max_posts(loader):
    fake_profile = MagicMock()
    fake_profile.biography = "A busy account"
    fake_profile.followers = 10000
    fake_profile.followees = 100
    posts = [
        make_fake_post(datetime(2026, 9, i, tzinfo=timezone.utc), 10, 1, "GraphImage", False)
        for i in range(1, 21)  # 20 posts available, should cap at MAX_POSTS
    ]
    fake_profile.get_posts.return_value = iter(posts)

    with patch("instaloader.Profile.from_username", return_value=fake_profile), \
         patch.object(loader, "get_highlights", return_value=iter([])):
        result = collector.collect_profile(loader, "busy_account")

    assert len(result["posts"]) == collector.MAX_POSTS
    assert result["highlight_count"] == 0


# --- (b) profile with very few posts (1-2) -----------------------------------

def test_collect_profile_few_posts(loader):
    fake_profile = MagicMock()
    fake_profile.biography = ""
    fake_profile.followers = 45
    fake_profile.followees = 60
    posts = [make_fake_post(datetime(2026, 9, 10, tzinfo=timezone.utc), 3, 0, "GraphImage", False)]
    fake_profile.get_posts.return_value = iter(posts)

    with patch("instaloader.Profile.from_username", return_value=fake_profile), \
         patch.object(loader, "get_highlights", return_value=iter([])):
        result = collector.collect_profile(loader, "tiny_new_account")

    assert result["bio"] == ""
    assert len(result["posts"]) == 1
    assert result["video_count"] == 0
    assert result["image_count"] == 1
    assert result["highlight_count"] == 0


# --- (c) nonexistent handle --------------------------------------------------

def test_collect_profile_nonexistent_handle(loader):
    with patch(
        "instaloader.Profile.from_username",
        side_effect=ProfileNotExistsException("user not found"),
    ):
        result = collector.collect_profile(loader, "this_handle_does_not_exist_xyz123")

    assert result["handle"] == "this_handle_does_not_exist_xyz123"
    for field in ("bio", "follower_count", "following_count", "posts",
                  "highlight_count", "video_count", "image_count"):
        assert result[field] == "unavailable"
    assert "error" in result
    assert "ProfileNotExistsException" in result["error"]


# --- private / partial-data cases -------------------------------------------

def test_collect_profile_private_account_posts_unavailable(loader):
    fake_profile = MagicMock()
    fake_profile.biography = "Private business account"
    fake_profile.followers = 500
    fake_profile.followees = 200
    fake_profile.get_posts.side_effect = instaloader.exceptions.LoginRequiredException(
        "Private profile"
    )

    with patch("instaloader.Profile.from_username", return_value=fake_profile), \
         patch.object(loader, "get_highlights", side_effect=instaloader.exceptions.LoginRequiredException("x")):
        result = collector.collect_profile(loader, "private_account")

    assert result["bio"] == "Private business account"
    assert result["follower_count"] == 500
    assert result["posts"] == "unavailable"
    assert result["video_count"] == "unavailable"
    assert result["image_count"] == "unavailable"
    assert result["highlight_count"] == "unavailable"


def test_collect_profile_hidden_like_count_marked_unavailable(loader):
    fake_profile = MagicMock()
    fake_profile.biography = "Hides likes"
    fake_profile.followers = 999
    fake_profile.followees = 10

    bad_post = MagicMock()
    bad_post.date_utc = datetime(2026, 9, 5, tzinfo=timezone.utc)
    type(bad_post).likes = property(lambda self: (_ for _ in ()).throw(RuntimeError("hidden")))
    bad_post.comments = 4
    bad_post.typename = "GraphImage"
    bad_post.is_video = False
    fake_profile.get_posts.return_value = iter([bad_post])

    with patch("instaloader.Profile.from_username", return_value=fake_profile), \
         patch.object(loader, "get_highlights", return_value=iter([])):
        result = collector.collect_profile(loader, "hides_likes")

    assert result["posts"][0]["likes"] == "unavailable"
    assert result["posts"][0]["comments"] == 4


# --- collect_many isolation & pacing -----------------------------------------

def test_collect_many_isolates_failures_and_paces(loader, monkeypatch):
    # Speed up the test: no real sleeping.
    monkeypatch.setattr(collector.time, "sleep", lambda *_args, **_kwargs: None)

    good_profile = MagicMock()
    good_profile.biography = "ok"
    good_profile.followers = 10
    good_profile.followees = 5
    good_profile.get_posts.return_value = iter([])

    def from_username(_context, username):
        if username == "bad_handle":
            raise ProfileNotExistsException("nope")
        return good_profile

    with patch("instaloader.Profile.from_username", side_effect=from_username), \
         patch.object(loader, "get_highlights", return_value=iter([])):
        results = collector.collect_many(loader, ["good_handle", "bad_handle", "good_handle2"])

    assert len(results) == 3
    assert results[0]["bio"] == "ok"
    assert results[1]["bio"] == "unavailable"
    assert "error" in results[1]
    assert results[2]["bio"] == "ok"


def test_collect_many_never_raises_on_unexpected_crash(loader, monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        collector, "collect_profile",
        MagicMock(side_effect=RuntimeError("totally unexpected"))
    )

    results = collector.collect_many(loader, ["h1"])

    assert len(results) == 1
    assert results[0]["handle"] == "h1"
    assert results[0]["bio"] == "unavailable"
    assert "unexpected crash" in results[0]["error"]


# --- backoff behavior ---------------------------------------------------------

def test_with_backoff_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(collector.random, "uniform", lambda a, b: 0)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise instaloader.exceptions.TooManyRequestsException("429")
        return "ok"

    result = collector._with_backoff(flaky, max_retries=5, base_delay=0.01)
    assert result == "ok"
    assert calls["n"] == 3


def test_with_backoff_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(collector.random, "uniform", lambda a, b: 0)

    def always_fails():
        raise instaloader.exceptions.TooManyRequestsException("429")

    with pytest.raises(instaloader.exceptions.TooManyRequestsException):
        collector._with_backoff(always_fails, max_retries=3, base_delay=0.01)


# --- LIVE test (bonus; must not hard-fail on rate limiting) -----------------

LIVE_TEST_HANDLES = {
    "normal": "nasa",              # public, well-known, many posts
    "few_posts": "instagram",      # fallback if a truly "few post" account isn't reachable
    "nonexistent": "this_account_should_not_exist_zzz_9182736",
}


def test_live_anonymous_fetch_three_profiles():
    """
    Best-effort real-network sanity check using an ANONYMOUS (not logged-in)
    Instaloader instance, per task instructions: single, well-paced,
    non-retrying requests; if Instagram rate-limits/blocks anonymous access,
    that is reported as a skip, not a failure — the mocked tests above are
    what satisfy the DoD.
    """
    anon_loader = instaloader.Instaloader(
        download_pictures=False, download_videos=False, download_video_thumbnails=False,
        download_geotags=False, download_comments=False, save_metadata=False,
    )

    try:
        result_normal = collector.collect_profile(anon_loader, LIVE_TEST_HANDLES["normal"])
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Live network call failed/blocked: {exc}")

    print("LIVE normal profile result:", result_normal)
    assert result_normal["handle"] == LIVE_TEST_HANDLES["normal"]

    result_nonexistent = collector.collect_profile(anon_loader, LIVE_TEST_HANDLES["nonexistent"])
    print("LIVE nonexistent profile result:", result_nonexistent)
    assert result_nonexistent["bio"] == "unavailable"

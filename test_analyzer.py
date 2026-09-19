"""
Module 4 tests -- Definition of Done per TRD.md:
"Run against 3 hand-crafted mock records with known expected outputs --
output matches exactly."

Expected outputs below were hand-computed against docs/Scoring-Spec.md
BEFORE running the code (see the worker report for the full by-hand
derivation). `now` is fixed to 2026-09-19T00:00:00+00:00 for determinism.
"""

import math
from datetime import datetime, timezone

from analyzer import analyze

NOW = datetime(2026, 9, 19, 0, 0, 0, tzinfo=timezone.utc)

MANUAL_REVIEW = [
    {"category": "visual_content_quality_gaps",
     "reason": "requires visual judgment (lighting/composition/image quality)"},
    {"category": "brand_consistency_issues",
     "reason": "requires visual judgment (brand/visual consistency)"},
]


# ---------------------------------------------------------------------------
# Mock A: strong/active cafe. Boundary tests: last post exactly 7 days ago
# (edge of the 0-7 bucket), reels_ratio exactly 0.4 (edge of the >=0.4 -> 0
# threshold), all mechanical checks present -> every scored category = 0.
# ---------------------------------------------------------------------------
RECORD_A = {
    "handle": "riverside_bakery",
    "bio": (
        "Fresh bread & pastries daily! 📍 Based in Koramangala, Bangalore. "
        "Order via WhatsApp wa.me/919999999999 or DM to book a table. "
        "Menu in our Highlights!"
    ),
    "follower_count": 2500,
    "following_count": 300,
    "posts": [
        {"date": "2026-09-12T00:00:00+00:00", "likes": 100, "comments": 10, "media_type": "video"},
        {"date": "2026-09-08T00:00:00+00:00", "likes": 90, "comments": 8, "media_type": "image"},
        {"date": "2026-09-01T00:00:00+00:00", "likes": 80, "comments": 5, "media_type": "video"},
        {"date": "2026-08-25T00:00:00+00:00", "likes": 70, "comments": 6, "media_type": "image"},
        {"date": "2026-08-10T00:00:00+00:00", "likes": 60, "comments": 4, "media_type": "image"},
    ],
    "highlight_count": 3,
    "video_count": 2,
    "image_count": 3,
    "captions": [
        "Fresh menu items today! Order for delivery.",
        "Thank you @janedoe for visiting us, best customer ever!",
        "New pastries dropped, check our menu",
    ],
    "highlight_names": ["Menu", "Reviews", "Delivery"],
}

_EXPECTED_A_AVG_ENGAGEMENT = ((100 + 10) / 2500 * 100 + (90 + 8) / 2500 * 100 + (80 + 5) / 2500 * 100) / 3

EXPECTED_A = {
    "handle": "riverside_bakery",
    "niche": "cafes/bakeries/home-food",
    "profile_metrics": {
        "follower_count": 2500,
        "follower_tier": "1000-3000",
        "last_post_recency_bucket": "0-7",
        "days_since_last_post": 7,
        "unavailable_reason": None,
        "engagement": {
            "average_engagement_rate": _EXPECTED_A_AVG_ENGAGEMENT,
            "engagement_sample_size": 3,
            "below_reference": False,
            "estimated_vs_observed": "directly_observed",
        },
    },
    "category_scores": {
        "posting_inactivity_or_inconsistency": 0,
        "profile_optimization_gaps": 0,
        "visual_content_quality_gaps": None,
        "limited_reels_or_video_content": 0,
        "niche_specific_content_gaps": 0,
        "missing_conversion_information": 0,
        "limited_visible_social_proof": 0,
        "brand_consistency_issues": None,
    },
    "total_score": 0,
    "manual_review": MANUAL_REVIEW,
    "flags": ["needs_manual_review"],
    "data_quality": {"unavailable_fields": []},
}


def test_mock_a_strong_active_cafe():
    actual = analyze(RECORD_A, "cafes/bakeries/home-food", now=NOW)
    actual_rate = actual["profile_metrics"]["engagement"].pop("average_engagement_rate")
    expected_rate = EXPECTED_A["profile_metrics"]["engagement"].pop("average_engagement_rate")
    assert math.isclose(actual_rate, expected_rate, rel_tol=1e-9)
    assert actual == EXPECTED_A


# ---------------------------------------------------------------------------
# Mock B: weak/inactive clinic. Boundary test: last post exactly 31 days ago
# (edge of the 31-90 bucket, one day past the 15-30 bucket). Only 2 total
# posts (forces category 1 to score 2 via the "fewer than 3 posts" rule),
# no useful bio content, no captions/highlight_names at all (today's real
# collector.py gap) -> every scored category = 2 (worst case).
# ---------------------------------------------------------------------------
RECORD_B = {
    "handle": "prime_dental_clinic",
    "bio": "Dental Clinic",
    "follower_count": 6000,
    "following_count": "unavailable",
    "posts": [
        {"date": "2026-08-19T00:00:00+00:00", "likes": "unavailable", "comments": 15, "media_type": "image"},
        {"date": "2026-06-01T00:00:00+00:00", "likes": 20, "comments": "unavailable", "media_type": "image"},
    ],
    "highlight_count": 0,
    "video_count": "unavailable",
    "image_count": "unavailable",
}

EXPECTED_B = {
    "handle": "prime_dental_clinic",
    "niche": "clinics/doctors/dentists/vets/pet-groomers",
    "profile_metrics": {
        "follower_count": 6000,
        "follower_tier": ">5000",
        "last_post_recency_bucket": "31-90",
        "days_since_last_post": 31,
        "unavailable_reason": None,
        "engagement": {
            "average_engagement_rate": "unavailable",
            "engagement_sample_size": 0,
            "below_reference": None,
            "estimated_vs_observed": "unavailable",
        },
    },
    "category_scores": {
        "posting_inactivity_or_inconsistency": 2,
        "profile_optimization_gaps": 2,
        "visual_content_quality_gaps": None,
        "limited_reels_or_video_content": 2,
        "niche_specific_content_gaps": 2,
        "missing_conversion_information": 2,
        "limited_visible_social_proof": 2,
        "brand_consistency_issues": None,
    },
    "total_score": 12,
    "manual_review": MANUAL_REVIEW,
    "flags": ["needs_manual_review", "captions_unavailable", "highlight_names_unavailable"],
    "data_quality": {
        "unavailable_fields": ["following_count", "video_count", "image_count", "captions", "highlight_names"],
    },
}


def test_mock_b_weak_inactive_clinic():
    assert analyze(RECORD_B, "clinics/doctors/dentists/vets/pet-groomers", now=NOW) == EXPECTED_B


# ---------------------------------------------------------------------------
# Mock C: mixed-unavailable tutoring account. Boundary tests: last post
# exactly 14 days ago (edge of the 8-14 bucket / the posting-inactivity
# "<=14 days" condition), a 10-day gap between posts in the last 30 days
# (exactly at the ">10 days" irregularity threshold -- must NOT count as
# irregular), and reels_ratio exactly 0.15 (edge of the >=0.15 -> 1
# threshold). follower_count is unavailable (forces engagement/tier to
# "unavailable"); highlight_count/following_count also unavailable.
# ---------------------------------------------------------------------------
_OLD_FILLER_POSTS = [
    {"date": "2025-01-01T00:00:00+00:00", "likes": 5, "comments": 1, "media_type": "image"}
    for _ in range(15)
]

RECORD_C = {
    "handle": "brightminds_tutoring",
    "bio": "Maths and Science tuition for CBSE grade 6-10 students.",
    "follower_count": "unavailable",
    "following_count": "unavailable",
    "posts": [
        {"date": "2026-09-05T00:00:00+00:00", "likes": 50, "comments": 5, "media_type": "video"},
        {"date": "2026-08-30T00:00:00+00:00", "likes": 40, "comments": 4, "media_type": "image"},
        {"date": "2026-08-20T00:00:00+00:00", "likes": 30, "comments": "unavailable", "media_type": "image"},
        {"date": "2026-08-01T00:00:00+00:00", "likes": 25, "comments": 3, "media_type": "video"},
        {"date": "2026-05-01T00:00:00+00:00", "likes": 10, "comments": 1, "media_type": "video"},
        *_OLD_FILLER_POSTS,
    ],
    "highlight_count": "unavailable",
    "video_count": 3,
    "image_count": 17,
    "captions": [
        "Tips for scoring well in exams!",
        "Syllabus overview for this term",
    ],
}

EXPECTED_C = {
    "handle": "brightminds_tutoring",
    "niche": "tutors/coaching",
    "profile_metrics": {
        "follower_count": "unavailable",
        "follower_tier": "unavailable",
        "last_post_recency_bucket": "8-14",
        "days_since_last_post": 14,
        "unavailable_reason": None,
        "engagement": {
            "average_engagement_rate": "unavailable",
            "engagement_sample_size": 0,
            "below_reference": None,
            "estimated_vs_observed": "unavailable",
        },
    },
    "category_scores": {
        "posting_inactivity_or_inconsistency": 0,
        "profile_optimization_gaps": 2,
        "visual_content_quality_gaps": None,
        "limited_reels_or_video_content": 1,
        "niche_specific_content_gaps": 1,
        "missing_conversion_information": 2,
        "limited_visible_social_proof": 2,
        "brand_consistency_issues": None,
    },
    "total_score": 8,
    "manual_review": MANUAL_REVIEW,
    "flags": ["needs_manual_review", "highlight_names_unavailable"],
    "data_quality": {
        "unavailable_fields": ["follower_count", "following_count", "highlight_count", "highlight_names"],
    },
}


def test_mock_c_mixed_unavailable_tutoring():
    assert analyze(RECORD_C, "tutors/coaching", now=NOW) == EXPECTED_C


# ---------------------------------------------------------------------------
# Small supplementary boundary checks (cheap, not part of the 3-mock DoD,
# but verify two rules the 3 main mocks don't otherwise exercise).
# ---------------------------------------------------------------------------

def test_no_posts_at_all_is_90_plus_with_reason():
    record = {"handle": "ghost_biz", "bio": UNAVAILABLE_BIO, "follower_count": 500,
              "following_count": 10, "posts": [], "highlight_count": 0,
              "video_count": 0, "image_count": 0}
    result = analyze(record, "restaurants", now=NOW)
    assert result["profile_metrics"]["last_post_recency_bucket"] == "90+"
    assert result["profile_metrics"]["unavailable_reason"] == "no posts"
    assert "verify_still_operating" in result["flags"]
    assert result["category_scores"]["posting_inactivity_or_inconsistency"] == 2


UNAVAILABLE_BIO = "unavailable"


def test_unknown_niche_defaults_to_conservative_niche_score():
    record = {"handle": "x", "bio": "hello", "follower_count": 100, "following_count": 1,
              "posts": [], "highlight_count": 0, "video_count": 0, "image_count": 0}
    result = analyze(record, "not-a-real-niche", now=NOW)
    assert result["category_scores"]["niche_specific_content_gaps"] == 2


def test_engagement_uses_3_most_recent_posts_not_list_order():
    """
    Regression test for a bug where _engagement() took the first 3 posts in
    whatever order `posts` happened to be given, instead of sorting
    newest-first first (like _recency()/_score_posting_inactivity() already
    do via _dated_posts_desc()). Scoring-Spec.md's "last 3 eligible posts"
    means the 3 MOST RECENT eligible posts, not "first 3 in list order."

    `posts` below is given oldest-first (the opposite of newest-first), and
    the two oldest posts are given deliberately huge likes/comments so the
    two possible interpretations diverge sharply:

    - Correct (3 most recent by date: 09-15, 09-10, 09-05):
      rates = 1.5, 2.5, 3.5 -> average_engagement_rate = 2.5
    - Buggy (first 3 in list order: 07-01, 08-01, 09-05):
      rates = 400, 200, 3.5 -> average_engagement_rate ~= 201.1667

    Against the pre-fix analyzer.py this test fails (computes ~201.1667,
    also flipping below_reference to False for the wrong reason and, in
    other cases, potentially flipping it the other way). Against the fixed
    analyzer.py it passes (computes exactly 2.5).
    """
    record = {
        "handle": "shuffled_order_biz",
        "bio": "unavailable",
        "follower_count": 1000,
        "following_count": 10,
        "posts": [
            {"date": "2026-07-01T00:00:00+00:00", "likes": 2000, "comments": 2000, "media_type": "image"},
            {"date": "2026-08-01T00:00:00+00:00", "likes": 1000, "comments": 1000, "media_type": "image"},
            {"date": "2026-09-05T00:00:00+00:00", "likes": 30, "comments": 5, "media_type": "image"},
            {"date": "2026-09-10T00:00:00+00:00", "likes": 20, "comments": 5, "media_type": "image"},
            {"date": "2026-09-15T00:00:00+00:00", "likes": 10, "comments": 5, "media_type": "image"},
        ],
        "highlight_count": 0,
        "video_count": 0,
        "image_count": 5,
    }
    result = analyze(record, "restaurants", now=NOW)
    engagement = result["profile_metrics"]["engagement"]
    expected_avg = ((10 + 5) / 1000 * 100 + (20 + 5) / 1000 * 100 + (30 + 5) / 1000 * 100) / 3
    assert math.isclose(expected_avg, 2.5, rel_tol=1e-9)  # sanity-check the hand calc itself
    assert math.isclose(engagement["average_engagement_rate"], expected_avg, rel_tol=1e-9)
    assert engagement["engagement_sample_size"] == 3
    assert engagement["below_reference"] is False


def test_engagement_undated_eligible_post_used_as_fallback_without_crashing():
    """
    Documents/verifies the deliberate decision in _engagement(): a post with
    valid likes/comments but an unparseable/missing date is still "eligible"
    per Scoring-Spec.md (eligibility only requires likes+comments visible,
    not a valid date) -- it just can't be placed in recency order, so it's
    only used to fill remaining slots after all date-orderable posts are
    considered. This must not crash and must still average correctly.
    """
    record = {
        "handle": "partial_dates_biz",
        "bio": "unavailable",
        "follower_count": 1000,
        "following_count": 10,
        "posts": [
            {"date": "2026-09-15T00:00:00+00:00", "likes": 10, "comments": 5, "media_type": "image"},
            {"date": "unavailable", "likes": 20, "comments": 5, "media_type": "image"},
        ],
        "highlight_count": 0,
        "video_count": 0,
        "image_count": 2,
    }
    result = analyze(record, "restaurants", now=NOW)
    engagement = result["profile_metrics"]["engagement"]
    expected_avg = ((10 + 5) / 1000 * 100 + (20 + 5) / 1000 * 100) / 2
    assert math.isclose(engagement["average_engagement_rate"], expected_avg, rel_tol=1e-9)
    assert engagement["engagement_sample_size"] == 2

import csv
import os
from datetime import datetime, timezone

from storage import CSV_COLUMNS, MANUAL_REVIEW_MARKER, flatten_record, write_leads_csv

# --- sample analyzer-output-shaped records (matching analyzer.analyze()'s real shape) ---

RECORD_FULL = {
    "handle": "the_cafe",
    "niche": "cafes_bakeries_home_food",
    "profile_metrics": {
        "follower_count": 1200,
        "follower_tier": "1,000-3,000",
        "last_post_recency_bucket": "0-7",
        "days_since_last_post": 3,
        "unavailable_reason": None,
        "engagement": {
            "average_engagement_rate": 4.5,
            "engagement_sample_size": 3,
            "below_reference": False,
            "estimated_vs_observed": "directly_observed",
        },
    },
    "category_scores": {
        "posting_inactivity_or_inconsistency": 0,
        "profile_optimization_gaps": 1,
        "visual_content_quality_gaps": None,
        "limited_reels_or_video_content": 2,
        "niche_specific_content_gaps": 1,
        "missing_conversion_information": 0,
        "limited_visible_social_proof": 2,
        "brand_consistency_issues": None,
    },
    "total_score": 6,
    "manual_review": [
        {"category": "visual_content_quality_gaps", "reason": "requires visual judgment (lighting/composition/image quality)"},
        {"category": "brand_consistency_issues", "reason": "requires visual judgment (brand/visual consistency)"},
    ],
    "flags": ["needs_manual_review"],
    "data_quality": {"unavailable_fields": []},
}

RECORD_UNAVAILABLE = {
    "handle": "ghost_biz",
    "niche": "restaurants",
    "profile_metrics": {
        "follower_count": "unavailable",
        "follower_tier": "unavailable",
        "last_post_recency_bucket": "90+",
        "days_since_last_post": None,
        "unavailable_reason": "no posts",
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
    "manual_review": [
        {"category": "visual_content_quality_gaps", "reason": "requires visual judgment (lighting/composition/image quality)"},
        {"category": "brand_consistency_issues", "reason": "requires visual judgment (brand/visual consistency)"},
    ],
    "flags": ["needs_manual_review", "verify_still_operating"],
    "data_quality": {"unavailable_fields": ["follower_count", "posts"]},
}

DISCOVERY_CANDIDATE = {
    "name": "The Cafe",
    "handle_guess": "the_cafe",
    "address": "123 Main St",
    "lat": 12.34,
    "lon": 56.78,
    "niche": "cafes_bakeries_home_food",
    "chain_group_id": None,
}


def test_flatten_record_full_values():
    row = flatten_record(RECORD_FULL, DISCOVERY_CANDIDATE)
    assert row["name"] == "The Cafe"
    assert row["address"] == "123 Main St"
    assert row["chain_group_id"] == ""  # None -> ''
    assert row["handle"] == "the_cafe"
    assert row["niche"] == "cafes_bakeries_home_food"
    assert row["follower_count"] == 1200
    assert row["engagement_average_rate"] == 4.5
    assert row["engagement_sample_size"] == 3
    assert row["total_score"] == 6
    assert row["flags"] == "needs_manual_review"
    assert row["manual_review_categories"] == "visual_content_quality_gaps;brand_consistency_issues"
    assert row["unavailable_fields"] == ""


def test_flatten_record_null_category_scores_use_marker():
    row = flatten_record(RECORD_FULL)
    assert row["visual_content_quality_gaps"] == MANUAL_REVIEW_MARKER
    assert row["brand_consistency_issues"] == MANUAL_REVIEW_MARKER
    # never a silent 0/blank for a null score
    assert row["visual_content_quality_gaps"] != 0
    assert row["visual_content_quality_gaps"] != ""
    # real 0 scores stay 0, visually distinguishable from the marker
    assert row["posting_inactivity_or_inconsistency"] == 0
    assert row["missing_conversion_information"] == 0


def test_flatten_record_unavailable_and_multiple_flags():
    row = flatten_record(RECORD_UNAVAILABLE)
    assert row["follower_count"] == "unavailable"
    assert row["engagement_average_rate"] == "unavailable"
    assert row["flags"] == "needs_manual_review;verify_still_operating"
    assert row["unavailable_fields"] == "follower_count;posts"


def test_write_leads_csv_headers_and_rows(tmp_path):
    output_dir = str(tmp_path / "output")
    now = datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)

    path = write_leads_csv(
        [(DISCOVERY_CANDIDATE, RECORD_FULL), RECORD_UNAVAILABLE],
        output_dir=output_dir,
        now=now,
    )

    assert path is not None
    assert os.path.basename(path) == "leads_2026-09-19_1300.csv"
    assert os.path.exists(path)

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == list(CSV_COLUMNS)
        rows = list(reader)

    assert len(rows) == 2
    assert rows[0]["handle"] == "the_cafe"
    assert rows[0]["name"] == "The Cafe"
    assert rows[0]["visual_content_quality_gaps"] == MANUAL_REVIEW_MARKER
    assert rows[1]["handle"] == "ghost_biz"
    assert rows[1]["name"] == ""  # no discovery candidate supplied for this row
    assert rows[1]["flags"] == "needs_manual_review;verify_still_operating"


def test_write_leads_csv_creates_output_dir(tmp_path):
    output_dir = str(tmp_path / "nested" / "output")
    assert not os.path.exists(output_dir)
    path = write_leads_csv([RECORD_FULL], output_dir=output_dir)
    assert path is not None
    assert os.path.exists(output_dir)


def test_write_leads_csv_returns_none_on_failure(tmp_path, monkeypatch):
    # Simulate a disk/permission failure by making os.makedirs raise.
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "makedirs", boom)
    path = write_leads_csv([RECORD_FULL], output_dir=str(tmp_path / "output"))
    assert path is None

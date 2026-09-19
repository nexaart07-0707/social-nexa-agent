"""
Module 5 (part 1) — Storage.

Owns: flattening analyzer.py's scored-record output (optionally merged with
a discovery.py candidate dict) into flat rows, and writing those rows to a
timestamped CSV under output/.

Does NOT own: scoring logic (analyzer.py) or notification (notifier.py) —
see Architecture.md module boundaries.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel written into a CSV cell for a category score that is None because
# the category is not auto-scored (visual_content_quality_gaps,
# brand_consistency_issues per Scoring-Spec.md) — must never look like a
# silent blank or a real 0.
MANUAL_REVIEW_MARKER = "manual_review"

CATEGORY_KEYS = (
    "posting_inactivity_or_inconsistency",
    "profile_optimization_gaps",
    "visual_content_quality_gaps",
    "limited_reels_or_video_content",
    "niche_specific_content_gaps",
    "missing_conversion_information",
    "limited_visible_social_proof",
    "brand_consistency_issues",
)

# Exact CSV column order.
CSV_COLUMNS = (
    # business-identifying fields (from discovery.py's candidate dict, if supplied)
    "name",
    "address",
    "chain_group_id",
    "handle",
    "niche",
    # profile metrics (from analyzer.py's profile_metrics block)
    "follower_count",
    "follower_tier",
    "last_post_recency_bucket",
    "days_since_last_post",
    "unavailable_reason",
    # engagement (from analyzer.py's profile_metrics.engagement block)
    "engagement_average_rate",
    "engagement_sample_size",
    "engagement_below_reference",
    "engagement_estimated_vs_observed",
    # category scores (0/1/2, or "manual_review" for the two non-scored categories)
    *CATEGORY_KEYS,
    "total_score",
    # list-shaped fields, flattened to ";"-joined strings
    "manual_review_categories",
    "flags",
    "unavailable_fields",
)


def _join(items: Any) -> str:
    """Join a list into a ';'-separated string; '' for empty/missing."""
    if not items:
        return ""
    return ";".join(str(i) for i in items)


def flatten_record(analyzer_output: dict, discovery_candidate: dict | None = None) -> dict:
    """
    Flatten one analyzer.py `analyze()` return dict (optionally merged with
    the discovery.py candidate dict it came from) into a single flat dict
    keyed by CSV_COLUMNS, ready to hand to csv.DictWriter.
    """
    discovery_candidate = discovery_candidate or {}
    profile_metrics = analyzer_output.get("profile_metrics", {}) or {}
    engagement = profile_metrics.get("engagement", {}) or {}
    category_scores = analyzer_output.get("category_scores", {}) or {}
    data_quality = analyzer_output.get("data_quality", {}) or {}

    row: dict[str, Any] = {
        "name": discovery_candidate.get("name", ""),
        "address": discovery_candidate.get("address", ""),
        "chain_group_id": discovery_candidate.get("chain_group_id") or "",
        "handle": analyzer_output.get("handle", ""),
        "niche": analyzer_output.get("niche", ""),
        "follower_count": profile_metrics.get("follower_count", ""),
        "follower_tier": profile_metrics.get("follower_tier", ""),
        "last_post_recency_bucket": profile_metrics.get("last_post_recency_bucket", ""),
        "days_since_last_post": profile_metrics.get("days_since_last_post", ""),
        "unavailable_reason": profile_metrics.get("unavailable_reason", ""),
        "engagement_average_rate": engagement.get("average_engagement_rate", ""),
        "engagement_sample_size": engagement.get("engagement_sample_size", ""),
        "engagement_below_reference": engagement.get("below_reference", ""),
        "engagement_estimated_vs_observed": engagement.get("estimated_vs_observed", ""),
        "total_score": analyzer_output.get("total_score", ""),
        "manual_review_categories": _join(
            m.get("category") for m in (analyzer_output.get("manual_review") or [])
        ),
        "flags": _join(analyzer_output.get("flags")),
        "unavailable_fields": _join(data_quality.get("unavailable_fields")),
    }

    for key in CATEGORY_KEYS:
        score = category_scores.get(key)
        row[key] = MANUAL_REVIEW_MARKER if score is None else score

    return row


def write_leads_csv(
    rows: list[dict | tuple[dict, dict]],
    output_dir: str = "output",
    now: datetime | None = None,
) -> str | None:
    """
    Write flattened analyzer records to a timestamped CSV under `output_dir`.

    Each entry in `rows` is either:
      - an analyzer.py `analyze()` output dict, or
      - a `(discovery_candidate, analyzer_output)` tuple, to also pull in
        business-identifying fields (name/address/chain_group_id).

    Returns the path written on success, or None on failure (disk full,
    permission error, etc. — logged, never raised, per Architecture.md's
    failure-isolation rule).
    """
    now = now or datetime.now(timezone.utc)
    filename = f"leads_{now.strftime('%Y-%m-%d_%H%M')}.csv"
    path = os.path.join(output_dir, filename)

    try:
        os.makedirs(output_dir, exist_ok=True)
        flattened = []
        for entry in rows:
            if isinstance(entry, tuple):
                discovery_candidate, analyzer_output = entry
            else:
                discovery_candidate, analyzer_output = None, entry
            flattened.append(flatten_record(analyzer_output, discovery_candidate))

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(flattened)

        logger.info("Wrote %d lead record(s) to %s", len(flattened), path)
        return path
    except OSError as exc:
        logger.error("Failed to write leads CSV to %s: %s", path, exc)
        return None

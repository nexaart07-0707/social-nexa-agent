from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

import storage
from notifier import (
    build_report_html,
    build_report_subject,
    load_scored_records_from_csv,
    send_daily_email_report,
)

RECORDS = [
    {"handle": "a", "flags": ["needs_manual_review"]},
    {"handle": "b", "flags": ["needs_manual_review", "verify_still_operating"]},
    {"handle": "c", "flags": []},
]


# ---------------------------------------------------------------------------
# Daily email report
# ---------------------------------------------------------------------------

SAMPLE_RECORD_HIGH_GAP = {
    "name": "Cafe Aroma",
    "address": "MG Road, Hubli",
    "chain_group_id": "",
    "handle": "cafe_aroma_hubli",
    "niche": "cafes/bakeries/home-food",
    "follower_count": 812,
    "follower_tier": "<1000",
    "last_post_recency_bucket": "31-90",
    "days_since_last_post": 42,
    "unavailable_reason": None,
    "engagement_average_rate": 1.35,
    "engagement_sample_size": 3,
    "engagement_below_reference": True,
    "engagement_estimated_vs_observed": "directly_observed",
    "posting_inactivity_or_inconsistency": 2,
    "profile_optimization_gaps": 1,
    "visual_content_quality_gaps": "manual_review",
    "limited_reels_or_video_content": 2,
    "niche_specific_content_gaps": 1,
    "missing_conversion_information": 2,
    "limited_visible_social_proof": 1,
    "brand_consistency_issues": "manual_review",
    "total_score": 9,
    "manual_review_categories": ["visual_content_quality_gaps", "brand_consistency_issues"],
    "flags": ["needs_manual_review"],
    "unavailable_fields": [],
}

SAMPLE_RECORD_LOW_GAP = {
    "name": "Sunrise Tutors",
    "address": "Vidyanagar, Hubli",
    "chain_group_id": "",
    "handle": "sunrise_tutors",
    "niche": "tutors/coaching",
    "follower_count": 6200,
    "follower_tier": ">5000",
    "last_post_recency_bucket": "0-7",
    "days_since_last_post": 3,
    "unavailable_reason": None,
    "engagement_average_rate": 4.2,
    "engagement_sample_size": 3,
    "engagement_below_reference": False,
    "engagement_estimated_vs_observed": "directly_observed",
    "posting_inactivity_or_inconsistency": 0,
    "profile_optimization_gaps": 0,
    "visual_content_quality_gaps": "manual_review",
    "limited_reels_or_video_content": 0,
    "niche_specific_content_gaps": 0,
    "missing_conversion_information": 0,
    "limited_visible_social_proof": 0,
    "brand_consistency_issues": "manual_review",
    "total_score": 0,
    "manual_review_categories": ["visual_content_quality_gaps", "brand_consistency_issues"],
    "flags": [],
    "unavailable_fields": [],
}


def test_build_report_subject():
    subject = build_report_subject("run1", date(2026, 9, 19))
    assert subject == "Daily Prospect Report - Run 1 (1PM-5PM IST) - 2026-09-19"


def test_build_report_html_contains_business_details():
    html = build_report_html("run1", [SAMPLE_RECORD_HIGH_GAP], date(2026, 9, 19))

    assert "Cafe Aroma" in html
    assert "cafes/bakeries/home-food" in html
    assert "MG Road, Hubli" in html
    assert "https://instagram.com/cafe_aroma_hubli" in html
    assert "812" in html
    assert "31-90" in html
    assert "1.35%" in html
    assert "needs_manual_review" in html
    assert "Total opportunity score: 9" in html
    assert "Visual content quality: needs manual review." in html
    assert "Brand consistency: needs manual review." in html
    # rule-based opportunity note, template-generated from category scores
    assert "High opportunity:" in html
    # single-column, inline CSS only -- no <style> blocks
    assert "<style" not in html
    assert "max-width:600px" in html


def test_build_report_html_low_gap_note():
    html = build_report_html("run2", [SAMPLE_RECORD_LOW_GAP], date(2026, 9, 19))
    assert "Low opportunity: no significant mechanical gaps detected." in html


def test_build_report_html_zero_results_states_clearly():
    html = build_report_html("run1", [], date(2026, 9, 19))
    assert "0 qualifying results." in html
    assert "No records to report for this run." in html


def test_build_report_html_below_minimum_states_clearly():
    html = build_report_html("run2", [SAMPLE_RECORD_HIGH_GAP], date(2026, 9, 19))
    assert "1 result, below the minimum of 2." in html


def test_send_daily_email_report_success_uses_correct_smtp_calls(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password-123")
    monkeypatch.setenv("GMAIL_SENDER_EMAIL", "nexaart07@gmail.com")
    monkeypatch.setenv("GMAIL_RECIPIENT_EMAIL", "nexaart07@gmail.com")

    mock_server = MagicMock()
    mock_smtp_cm = MagicMock()
    mock_smtp_cm.__enter__.return_value = mock_server
    mock_smtp_cm.__exit__.return_value = False

    with patch("notifier.smtplib.SMTP", return_value=mock_smtp_cm) as mock_smtp:
        result = send_daily_email_report("run1", [SAMPLE_RECORD_HIGH_GAP], date(2026, 9, 19))

    assert result is True
    mock_smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=30)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("nexaart07@gmail.com", "app-password-123")
    mock_server.send_message.assert_called_once()

    sent_message = mock_server.send_message.call_args[0][0]
    assert sent_message["From"] == "nexaart07@gmail.com"
    assert sent_message["To"] == "nexaart07@gmail.com"
    assert "Run 1" in sent_message["Subject"]
    assert "2026-09-19" in sent_message["Subject"]


def test_send_daily_email_report_zero_results_still_sends(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password-123")

    mock_server = MagicMock()
    mock_smtp_cm = MagicMock()
    mock_smtp_cm.__enter__.return_value = mock_server
    mock_smtp_cm.__exit__.return_value = False

    with patch("notifier.smtplib.SMTP", return_value=mock_smtp_cm):
        result = send_daily_email_report("run2", [], date(2026, 9, 19))

    assert result is True
    sent_message = mock_server.send_message.call_args[0][0]
    html_payload = sent_message.get_payload()[0].get_payload()
    assert "0 qualifying results." in html_payload


def test_send_daily_email_report_missing_app_password_logs_error(monkeypatch, caplog):
    """No second channel to fall back to (Telegram removed) -- a missing
    GMAIL_APP_PASSWORD must just be logged clearly, not silently dropped."""
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    with patch("notifier.smtplib.SMTP") as mock_smtp, caplog.at_level("ERROR"):
        result = send_daily_email_report("run1", [SAMPLE_RECORD_HIGH_GAP], date(2026, 9, 19))

    assert result is False
    mock_smtp.assert_not_called()
    assert "Daily email report FAILED for run1" in caplog.text
    assert "GMAIL_APP_PASSWORD" in caplog.text


def test_send_daily_email_report_smtp_exception_logs_error(monkeypatch, caplog):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password-123")

    with patch("notifier.smtplib.SMTP", side_effect=OSError("connection refused")), \
         caplog.at_level("ERROR"):
        result = send_daily_email_report("run2", [SAMPLE_RECORD_HIGH_GAP], date(2026, 9, 19))

    assert result is False
    assert "Daily email report FAILED for run2" in caplog.text
    assert "connection refused" in caplog.text


def test_send_daily_email_report_failure_does_not_raise(monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    result = send_daily_email_report("run1", [SAMPLE_RECORD_HIGH_GAP], date(2026, 9, 19))
    assert result is False


# ---------------------------------------------------------------------------
# load_scored_records_from_csv
# ---------------------------------------------------------------------------

def test_load_scored_records_from_csv_roundtrip(tmp_path):
    analyzer_output = {
        "handle": "cafe_aroma_hubli",
        "niche": "cafes/bakeries/home-food",
        "profile_metrics": {
            "follower_count": 812,
            "follower_tier": "<1000",
            "last_post_recency_bucket": "31-90",
            "days_since_last_post": 42,
            "unavailable_reason": None,
            "engagement": {
                "average_engagement_rate": 1.35,
                "engagement_sample_size": 3,
                "below_reference": True,
                "estimated_vs_observed": "directly_observed",
            },
        },
        "category_scores": {
            "posting_inactivity_or_inconsistency": 2,
            "profile_optimization_gaps": 1,
            "visual_content_quality_gaps": None,
            "limited_reels_or_video_content": 2,
            "niche_specific_content_gaps": 1,
            "missing_conversion_information": 2,
            "limited_visible_social_proof": 1,
            "brand_consistency_issues": None,
        },
        "total_score": 9,
        "manual_review": [
            {"category": "visual_content_quality_gaps", "reason": "x"},
            {"category": "brand_consistency_issues", "reason": "y"},
        ],
        "flags": ["needs_manual_review"],
        "data_quality": {"unavailable_fields": ["captions"]},
    }
    discovery_candidate = {"name": "Cafe Aroma", "address": "MG Road, Hubli", "chain_group_id": None}

    csv_path = storage.write_leads_csv(
        [(discovery_candidate, analyzer_output)],
        output_dir=str(tmp_path),
        now=datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc),
    )
    assert csv_path is not None

    records = load_scored_records_from_csv(csv_path)
    assert len(records) == 1
    record = records[0]

    assert record["name"] == "Cafe Aroma"
    assert record["follower_count"] == 812
    assert record["days_since_last_post"] == 42
    assert record["total_score"] == 9
    assert record["engagement_average_rate"] == 1.35
    assert record["engagement_sample_size"] == 3
    assert record["engagement_below_reference"] is True
    assert record["posting_inactivity_or_inconsistency"] == 2
    assert record["visual_content_quality_gaps"] == "manual_review"
    assert record["flags"] == ["needs_manual_review"]
    assert record["manual_review_categories"] == [
        "visual_content_quality_gaps", "brand_consistency_issues",
    ]
    assert record["unavailable_fields"] == ["captions"]

    # And the loaded record must be directly usable by the email builder.
    html = build_report_html("run1", records, date(2026, 9, 19))
    assert "Cafe Aroma" in html
    assert "Total opportunity score: 9" in html

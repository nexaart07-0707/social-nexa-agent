"""
Module 5 (part 2) — Notification.

Owns: sending the daily prospect-report email. Does NOT own scoring or CSV
writing — see Architecture.md module boundaries.

Email (Gmail SMTP) is the SOLE notification/reporting channel for this
project — there is no second channel to fall back to. This covers both the
1PM/5PM research runs (each sends its own report immediately on completion,
via orchestrator.py's call to send_daily_email_report()) and the 8PM
consolidated report (orchestrator.py --report).
"""

from __future__ import annotations

import csv
import html as _html
import logging
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import storage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Daily email report (Module 5)
#
# Sends a run's report as a self-send HTML email via Gmail SMTP (stdlib
# smtplib + email.mime — no new dependency).
#
# Credentials (never hardcoded):
#   GMAIL_APP_PASSWORD    — required; a Gmail App Password (not the account
#                           password). Generate at
#                           https://myaccount.google.com/apppasswords after
#                           enabling 2-Step Verification on the account.
#   GMAIL_SENDER_EMAIL    — defaults to nexaart07@gmail.com
#   GMAIL_RECIPIENT_EMAIL — defaults to nexaart07@gmail.com (self-send)
# ---------------------------------------------------------------------------

_DEFAULT_GMAIL_ACCOUNT = "nexaart07@gmail.com"

_RUN_LABELS = {
    "run1": "Run 1 (1PM-5PM IST)",
    "run2": "Run 2 (5PM-7PM IST)",
}

# Mirrors scheduler_rules._RULES min values, duplicated here (not imported)
# to keep notifier.py decoupled from scheduler_rules — this is display-only
# wording, not enforcement.
_RUN_MIN_FOR_DISPLAY = {"run1": 2, "run2": 2}

_CATEGORY_LABELS = {
    "posting_inactivity_or_inconsistency": "Posting inactivity/inconsistency",
    "profile_optimization_gaps": "Profile optimization gaps",
    "visual_content_quality_gaps": "Visual content quality",
    "limited_reels_or_video_content": "Limited Reels/video content",
    "niche_specific_content_gaps": "Niche-specific content gaps",
    "missing_conversion_information": "Missing conversion information",
    "limited_visible_social_proof": "Limited visible social proof",
    "brand_consistency_issues": "Brand consistency",
}

# Template phrases for the rule-based opportunity note (category -> short
# gap description). Deliberately excludes the two manual-review categories
# (visual_content_quality_gaps, brand_consistency_issues) since those are
# never auto-scored 0/1/2 -- see docs/Scoring-Spec.md.
_OPPORTUNITY_PHRASES = {
    "posting_inactivity_or_inconsistency": "inactive posting",
    "profile_optimization_gaps": "an under-optimized profile",
    "limited_reels_or_video_content": "little video/Reels content",
    "niche_specific_content_gaps": "niche-specific content gaps",
    "missing_conversion_information": "no clear booking/contact link",
    "limited_visible_social_proof": "little visible social proof",
}


def _escape(value: Any) -> str:
    if value is None or value == "":
        return "unavailable"
    return _html.escape(str(value))


def _opportunity_note(record: dict) -> str:
    """Deterministic, template-based one-line opportunity note synthesized
    from category_scores -- NOT free-text generation (per project's
    rule-based-only constraint). analyzer.py has no dedicated opportunity-
    note field, so this fills that gap per the task's instruction.
    """
    clear_gaps = [phrase for key, phrase in _OPPORTUNITY_PHRASES.items() if record.get(key) == 2]
    if clear_gaps:
        return "High opportunity: " + " and ".join(clear_gaps[:2]) + "."
    moderate_gaps = [phrase for key, phrase in _OPPORTUNITY_PHRASES.items() if record.get(key) == 1]
    if moderate_gaps:
        return "Moderate opportunity: " + " and ".join(moderate_gaps[:2]) + "."
    return "Low opportunity: no significant mechanical gaps detected."


def _manual_review_notes(record: dict) -> list[str]:
    categories = record.get("manual_review_categories") or []
    return [f"{_CATEGORY_LABELS.get(c, c)}: needs manual review." for c in categories]


def _business_block_html(record: dict) -> str:
    """One responsive, inline-CSS-only HTML block for a single business."""
    name = _escape(record.get("name") or record.get("handle"))
    niche = _escape(record.get("niche", ""))
    location = _escape(record.get("address", ""))
    handle = record.get("handle") or ""
    ig_link = f"https://instagram.com/{handle}" if handle else ""

    followers = _escape(record.get("follower_count"))
    recency = _escape(record.get("last_post_recency_bucket"))
    days = _escape(record.get("days_since_last_post"))
    total_score = _escape(record.get("total_score"))

    engagement = record.get("engagement_average_rate")
    if isinstance(engagement, (int, float)):
        engagement_display = f"{engagement:.2f}%"
    else:
        engagement_display = _escape(engagement)

    flags = record.get("flags") or []
    flags_html = "".join(
        f'<li style="margin:0 0 4px 0;">{_escape(f)}</li>' for f in flags
    ) or '<li style="margin:0 0 4px 0;">none</li>'

    review_notes = _manual_review_notes(record)
    review_section = ""
    if review_notes:
        review_items = "".join(
            f'<li style="margin:0 0 4px 0;">{_escape(n)}</li>' for n in review_notes
        )
        review_section = (
            '<div style="font-size:13px;color:#8a5b00;margin:0 0 8px 0;">'
            '<strong>Manual review needed:</strong>'
            f'<ul style="margin:4px 0 0 18px;padding:0;">{review_items}</ul>'
            "</div>"
        )

    note = _escape(_opportunity_note(record))
    ig_html = (
        f'<a href="{_escape(ig_link)}" style="color:#1a73e8;text-decoration:none;">{_escape(ig_link)}</a>'
        if ig_link else "unavailable"
    )

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;width:100%;margin:0 0 16px 0;background:#ffffff;'
        'border:1px solid #e0e0e0;border-radius:8px;">'
        '<tr><td style="padding:16px;font-family:Arial,Helvetica,sans-serif;">'
        f'<div style="font-size:18px;font-weight:bold;color:#1a1a1a;margin:0 0 4px 0;">{name}</div>'
        f'<div style="font-size:14px;color:#555555;margin:0 0 8px 0;">{niche} &middot; {location}</div>'
        f'<div style="font-size:14px;margin:0 0 8px 0;">{ig_html}</div>'
        '<div style="font-size:14px;color:#333333;line-height:1.6;margin:0 0 8px 0;">'
        f"Followers: {followers}<br>"
        f"Last post: {recency} ({days} days)<br>"
        f"Engagement rate: {engagement_display}<br>"
        f"Total opportunity score: {total_score}"
        "</div>"
        '<div style="font-size:13px;color:#555555;margin:0 0 8px 0;">'
        "<strong>Weak signals:</strong>"
        f'<ul style="margin:4px 0 0 18px;padding:0;">{flags_html}</ul>'
        "</div>"
        f"{review_section}"
        f'<div style="font-size:14px;color:#1a1a1a;font-weight:bold;">{note}</div>'
        "</td></tr></table>"
    )


def _report_summary_line(run: str, count: int) -> str:
    min_required = _RUN_MIN_FOR_DISPLAY.get(run)
    if count == 0:
        return "0 qualifying results."
    if min_required is not None and count < min_required:
        return f"{count} result{'s' if count != 1 else ''}, below the minimum of {min_required}."
    return f"{count} qualifying result{'s' if count != 1 else ''}."


def build_report_subject(run: str, run_date: date) -> str:
    run_label = _RUN_LABELS.get(run, run)
    return f"Daily Prospect Report - {run_label} - {run_date.isoformat()}"


def build_report_html(run: str, scored_records: list[dict], run_date: date) -> str:
    """Build the single-column, max-width-600px, inline-CSS-only HTML body
    for one run's daily report. Always produces a full report -- including
    the zero-results / below-minimum case -- per the task's "still send the
    email" requirement.
    """
    run_label = _escape(_RUN_LABELS.get(run, run))
    summary = _escape(_report_summary_line(run, len(scored_records)))

    blocks = "".join(_business_block_html(r) for r in scored_records)
    if not blocks:
        blocks = (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            'color:#555555;padding:8px 0;">No records to report for this run.</div>'
        )

    return (
        '<div style="background:#f4f4f4;padding:16px 0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;width:100%;margin:0 auto;">'
        '<tr><td style="font-family:Arial,Helvetica,sans-serif;padding:0 16px 16px 16px;">'
        '<div style="font-size:20px;font-weight:bold;color:#1a1a1a;margin:0 0 8px 0;">'
        f"Daily Prospect Report &mdash; {run_label} &mdash; {_escape(run_date.isoformat())}"
        "</div>"
        f'<div style="font-size:15px;color:#333333;margin:0 0 16px 0;">{summary}</div>'
        "</td></tr>"
        f'<tr><td style="padding:0 16px;">{blocks}</td></tr>'
        "</table></div>"
    )


def send_daily_email_report(
    run: str,
    scored_records: list[dict],
    run_date: date,
) -> bool:
    """
    Send that day's report for one run (`run` is "run1" or "run2") as a
    self-send HTML email via Gmail SMTP. `scored_records` is a list of flat
    dicts shaped like storage.flatten_record()'s output (list-valued fields
    -- flags/manual_review_categories/unavailable_fields -- as real lists,
    not ";"-joined strings); use load_scored_records_from_csv() to build
    this list from a CSV written earlier by storage.write_leads_csv().

    Still sends an email for zero-result or below-minimum runs (states that
    plainly, per the reporting requirement) -- never skips silently.

    Never raises (Architecture.md failure-isolation pattern, same as every
    other external call in this codebase): on any send failure, logs the
    error clearly and returns False. Email is the sole notification channel
    now (Telegram was removed) so there is no fallback channel to send to --
    a send failure is only ever surfaced via the GitHub Actions log.
    """
    # ponytail: GitHub Actions' workflow env: block sets these vars to an
    # EMPTY STRING (not unset) when the referenced secret doesn't exist --
    # os.environ.get(key, default) only falls back on a missing key, not an
    # empty value, so `or` is required here to actually apply the default.
    # Found live: every GMAIL_APP_PASSWORD rotation kept failing with a Gmail
    # auth error because sender was silently resolving to '' (login with a
    # blank username), not because any password was ever actually wrong.
    sender = os.environ.get("GMAIL_SENDER_EMAIL") or _DEFAULT_GMAIL_ACCOUNT
    recipient = os.environ.get("GMAIL_RECIPIENT_EMAIL") or _DEFAULT_GMAIL_ACCOUNT
    app_password = os.environ.get("GMAIL_APP_PASSWORD")

    subject = build_report_subject(run, run_date)
    html_body = build_report_html(run, scored_records, run_date)

    try:
        if not app_password:
            raise RuntimeError(
                "GMAIL_APP_PASSWORD not set -- generate one at "
                "https://myaccount.google.com/apppasswords after enabling "
                "2-Step Verification, then set the env var."
            )

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        message.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(sender, app_password)
            server.send_message(message)

        logger.info("Daily email report sent for %s (%s)", run, run_date.isoformat())
        return True

    except Exception as exc:  # noqa: BLE001 -- deliberate broad catch, see docstring
        # No second channel to fall back to (Telegram removed) -- just log
        # clearly so a human watching the GitHub Actions log sees it.
        logger.error(
            "Daily email report FAILED for %s (%s): %s", run, run_date.isoformat(), exc
        )
        return False


def load_scored_records_from_csv(path: str) -> list[dict]:
    """
    Read a CSV written by storage.write_leads_csv() back into a list of
    flat dicts in the same shape send_daily_email_report() expects: numeric
    columns coerced back to int/float, and the three ";"-joined list
    columns (flags, manual_review_categories, unavailable_fields)
    reconstructed into real lists.

    For a real deployment where the 8 PM report cron job is a separate
    process from the 1 PM/5 PM research runs, this is how it recovers that
    day's already-scored data without re-running discovery/collection.
    """
    records: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            record: dict[str, Any] = dict(row)

            for list_field in ("flags", "manual_review_categories", "unavailable_fields"):
                raw = record.get(list_field) or ""
                record[list_field] = [item for item in raw.split(";") if item]

            for key in storage.CATEGORY_KEYS:
                value = record.get(key, "")
                if value and value != storage.MANUAL_REVIEW_MARKER:
                    try:
                        record[key] = int(value)
                    except ValueError:
                        pass

            for int_field in ("follower_count", "days_since_last_post", "total_score", "engagement_sample_size"):
                value = record.get(int_field, "")
                if value not in ("", None):
                    try:
                        record[int_field] = int(float(value))
                    except ValueError:
                        pass

            rate = record.get("engagement_average_rate", "")
            if rate not in ("", None):
                try:
                    record["engagement_average_rate"] = float(rate)
                except ValueError:
                    pass

            below_ref = record.get("engagement_below_reference", "")
            record["engagement_below_reference"] = {"True": True, "False": False}.get(below_ref, None)

            records.append(record)

    return records

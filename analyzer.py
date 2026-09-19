"""
Module 4 -- Rule-Based Analyzer

Owns: pure scoring of one collected profile record against
docs/Scoring-Spec.md. Does NOT own: any network/file I/O, discovery,
or collection (those belong to Modules 1-3).

PURE MODULE: no instaloader/requests/file I/O imports. `analyze()` takes a
plain dict (Module 3's output shape) plus a niche string and returns a
plain dict. The only "impure" input is the `now` parameter, which defaults
to the system clock but can be passed explicitly for deterministic testing.

--------------------------------------------------------------------------
KNOWN GAPS between Module 3's current real output and what this module's
spec-mandated checks would ideally use (documented per task instructions,
NOT fixed by changing collector.py, which is out of scope for Module 4):

1. `captions` (list[str] of recent post captions) -- collector.py's
   documented contract does NOT include this field at all. Scoring-Spec.md
   category 5 (niche-specific) and category 7 (social proof) need caption
   text for some checks (e.g. tutors' "educational-content caption
   keyword", "customer tags/mentions in recent captions"). This module
   reads `record.get("captions", "unavailable")` -- if the field is simply
   absent (today's reality) or explicitly "unavailable", every check that
   needs captions degrades gracefully to its worse/conservative mechanical
   fallback instead of crashing or guessing.

2. `highlight_names` (list[str] of Story Highlight titles) -- collector.py
   only produces `highlight_count` (an int), never the Highlight *names*.
   Several spec checks are keyed on Highlight names specifically (a
   "Menu" Highlight, a "Reviews"/"Testimonials" Highlight, "sizing/
   pricing/delivery"-named Highlights for boutiques). This module reads
   `record.get("highlight_names", "unavailable")`. When absent (today's
   reality), those specific name-based checks always resolve to "missing"
   -- notably this means the boutiques/clothing/jewelry niche's first
   mechanical check ("Highlights include sizing/pricing/delivery-named
   sections") can NEVER be satisfied until Module 3 is extended to expose
   Highlight names, which will systematically push that niche's category 5
   score up by one missing-check. This is flagged in the module's output
   via the `highlight_names_unavailable` flag on every record where it
   applies, so it stays visible downstream rather than silently biasing
   scores.

Both gaps are handled via `.get(field, "unavailable")` -- this module works
standalone against collector.py's exact current output shape.
--------------------------------------------------------------------------

Additional [OPERATIONALIZED] decisions made here, beyond the ones already
marked in Scoring-Spec.md (flagged for manager review same as the spec's
own [OPERATIONALIZED] items):

- "Burst-then-silence pattern in the last 90 days" (category 1, spec line
  for score `1`) has no numeric definition in the spec. Operationalized as:
  a gap > 30 days between two consecutive posts (both dated within the
  last 90 days), where at least 2 posts landed within the 7 days
  immediately before the gap started (the "burst").
- "Location mentioned" (used by categories 2 and 6, and by several niche
  checks) is operationalized as a regex match for a pin emoji or the
  keywords "located" / "location" / "based in" / "address" in the bio --
  the analyzer has no ground-truth city/address to compare against (that
  data lives in Module 2's candidate record, not in Module 3's record
  passed here), so only presence of a location cue is checkable.
- "Services listed" / CTA / booking-link / hours / appointment / review
  detection all use small literal keyword and regex lists documented as
  constants at the top of this file. These are pragmatic, not exhaustive.
- Any presence/absence checklist item (categories 2, 5, 6, 7) that depends
  on a field marked "unavailable" is conservatively counted as MISSING
  (the checklist cannot confirm it is there) -- this is the documented
  graceful-degradation behavior, distinct from spec rule #8 ("never treat
  an unavailable *metric* as zero"), which applies to numeric metrics like
  follower/engagement math, not boolean presence checks.
- category 4 (Reels ratio) and the reels-ratio fallback: if neither a
  usable `posts` list nor numeric `video_count`/`image_count` are
  available, or total posts is 0, the score conservatively defaults to `2`
  (same bucket the spec assigns to `total_posts == 0`).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

UNAVAILABLE = "unavailable"

NICHE_KEYS = (
    "cafes/bakeries/home-food",
    "tutors/coaching",
    "restaurants",
    "salons/spas/beauty",
    "boutiques/clothing/jewelry",
    "clinics/doctors/dentists/vets/pet-groomers",
)

# --- keyword / regex constants (all case-insensitive) --------------------

_PHONE_RE = re.compile(r"(\+?\d[\d\-\s()]{7,}\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BOOKING_DOMAINS = (
    "linktr.ee", "linkin.bio", "calendly.com", "wa.me", "api.whatsapp.com",
    "bit.ly", "square.site", "swiggy", "zomato", "doordash", "ubereats",
)
_CTA_RE = re.compile(
    r"\b(dm us|dm to book|book now|order now|shop now|call now|visit us|"
    r"contact us|link in bio|click the link|message us|whatsapp)\b", re.I,
)
_LOCATION_RE = re.compile(r"📍|\b(located|location|based in|address)\b", re.I)
_ORDER_DELIVERY_RE = re.compile(
    r"\b(order|ordering|delivery|takeaway|swiggy|zomato|doordash|ubereats)\b", re.I,
)
_HOURS_RE = re.compile(
    r"\b(mon|tue|wed|thu|fri|sat|sun|open daily|hours|\d{1,2}\s?(am|pm))\b", re.I,
)
_RESERVATION_RE = re.compile(r"\b(reservation|reserve|book a table)\b", re.I)
_APPOINTMENT_RE = re.compile(r"\b(appointment|schedule|consult)\b", re.I)
_SERVICES_RE = re.compile(r"\b(services?|treatments?|packages?)\b", re.I)
_SUBJECT_RE = re.compile(
    r"\b(grade\s?\d+|class\s?\d+|board|cbse|icse|\bib\b|subjects?|maths?|"
    r"science|english|physics|chemistry|biology)\b", re.I,
)
_ENROLLMENT_RE = re.compile(r"\b(enroll|enrolment|enrollment|admission|registration)\b", re.I)
_EDU_CAPTION_RE = re.compile(r"\b(tips|exam|syllabus|homework|lesson)\b", re.I)
_REVIEW_MENTION_RE = re.compile(r"\b\d+\+?\s*(google\s*)?reviews?\b|★|\b5[\s-]?star\b", re.I)
_CUSTOMER_MENTION_RE = re.compile(r"@\w+|\b(customer|client)\b", re.I)
_CATALOG_LINK_RE = re.compile(r"\b(catalog|catalogue|shop now|our store|store link)\b", re.I)
_SIZING_PRICING_DELIVERY_RE = re.compile(r"siz(e|ing)|pricing|price|delivery", re.I)


def _text_available(value: Any) -> bool:
    return isinstance(value, str) and value != UNAVAILABLE


def _has_contact(text: str) -> bool:
    return bool(_PHONE_RE.search(text) or _EMAIL_RE.search(text))


def _has_booking_link(text: str) -> bool:
    lowered = text.lower()
    return any(domain in lowered for domain in _BOOKING_DOMAINS)


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or value == UNAVAILABLE:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _dated_posts_desc(posts: list[dict]) -> list[tuple[datetime, dict]]:
    """Posts with a parseable date, sorted newest-first."""
    dated = [(dt, p) for p in posts if (dt := _parse_date(p.get("date"))) is not None]
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return dated


# --- reported metrics ------------------------------------------------------

def _recency(posts: Any, now: datetime) -> dict:
    if posts == UNAVAILABLE or not isinstance(posts, list):
        return {"bucket": UNAVAILABLE, "days_since_last_post": UNAVAILABLE,
                 "unavailable_reason": "post data unavailable"}
    if len(posts) == 0:
        return {"bucket": "90+", "days_since_last_post": UNAVAILABLE,
                 "unavailable_reason": "no posts"}
    dated = _dated_posts_desc(posts)
    if not dated:
        return {"bucket": UNAVAILABLE, "days_since_last_post": UNAVAILABLE,
                 "unavailable_reason": "post dates unavailable"}
    days = max((now - dated[0][0]).days, 0)
    if days <= 7:
        bucket = "0-7"
    elif days <= 14:
        bucket = "8-14"
    elif days <= 30:
        bucket = "15-30"
    elif days <= 90:
        bucket = "31-90"
    else:
        bucket = "90+"
    return {"bucket": bucket, "days_since_last_post": days, "unavailable_reason": None}


def _follower_tier(follower_count: Any) -> str:
    if follower_count == UNAVAILABLE or not isinstance(follower_count, (int, float)):
        return UNAVAILABLE
    if follower_count < 1000:
        return "<1000"
    if follower_count < 3000:
        return "1000-3000"
    if follower_count <= 5000:
        return "3000-5000"
    return ">5000"


def _engagement(posts: Any, follower_count: Any) -> dict:
    unavailable_result = {
        "average_engagement_rate": UNAVAILABLE, "engagement_sample_size": 0,
        "below_reference": None, "estimated_vs_observed": UNAVAILABLE,
    }
    if follower_count == UNAVAILABLE or not isinstance(follower_count, (int, float)) or follower_count <= 0:
        return unavailable_result
    if not isinstance(posts, list):
        return unavailable_result

    # Scoring-Spec.md: "the last 3 eligible posts" means the 3 MOST RECENT
    # eligible posts, not the first 3 in whatever order `posts` happens to be
    # in -- so sort newest-first the same way _recency()/_score_posting_
    # inactivity() already do, via _dated_posts_desc(), instead of trusting
    # input order.
    #
    # _dated_posts_desc() drops posts with an unparseable/missing date. The
    # spec's eligibility test for engagement is only "likes and comments
    # visible" -- it says nothing about needing a valid date -- so a post
    # with real likes/comments but no usable date is still eligible; it just
    # can't be placed in recency order. We resolve this by considering
    # date-ordered (newest-first) posts first, then falling back to any
    # remaining undated-but-eligible posts (in their original list order) to
    # fill out the 3-post sample. This keeps recency ordering wherever it's
    # knowable and only reaches for unorderable posts as a last resort.
    dated_posts = [p for _, p in _dated_posts_desc(posts)]
    undated_posts = [p for p in posts if _parse_date(p.get("date")) is None]
    ordered_posts = dated_posts + undated_posts

    rates = []
    for post in ordered_posts:
        likes, comments = post.get("likes"), post.get("comments")
        if likes == UNAVAILABLE or comments == UNAVAILABLE:
            continue
        if not isinstance(likes, (int, float)) or not isinstance(comments, (int, float)):
            continue
        rates.append((likes + comments) / follower_count * 100)
        if len(rates) == 3:
            break

    if not rates:
        return unavailable_result
    avg = sum(rates) / len(rates)
    return {
        "average_engagement_rate": avg,
        "engagement_sample_size": len(rates),
        "below_reference": avg < 2,
        "estimated_vs_observed": "directly_observed",
    }


# --- category 1: posting inactivity or inconsistency ------------------------

def _score_posting_inactivity(posts: Any, recency_days: Any, now: datetime) -> int:
    if not isinstance(posts, list) or len(posts) < 3:
        return 2
    if recency_days in (None, UNAVAILABLE):
        return 2
    if recency_days > 30:
        return 2
    if 15 <= recency_days <= 30:
        return 1

    # recency_days <= 14 from here on
    dated_asc = [dt for dt, _ in reversed(_dated_posts_desc(posts))]

    def days_since(dt: datetime) -> int:
        return (now - dt).days

    window30 = [dt for dt in dated_asc if days_since(dt) <= 30]
    posts_last_30 = len(window30)
    gap_over_10_in_30 = any(
        (window30[i + 1] - window30[i]).days > 10 for i in range(len(window30) - 1)
    )

    window90 = [dt for dt in dated_asc if days_since(dt) <= 90]
    burst_then_silence = False
    for i in range(len(window90) - 1):
        gap = (window90[i + 1] - window90[i]).days
        if gap > 30:
            burst_count = sum(1 for dt in window90[: i + 1] if (window90[i] - dt).days <= 7)
            if burst_count >= 2:
                burst_then_silence = True
                break

    if posts_last_30 >= 3 and not gap_over_10_in_30 and not burst_then_silence:
        return 0
    return 1


# --- category 2: profile optimization gaps ----------------------------------

def _score_profile_optimization(bio: Any, highlight_count: Any) -> int:
    bio_ok = _text_available(bio) and bio.strip() != ""
    bio_text = bio if _text_available(bio) else ""

    missing = 0
    if not bio_ok:
        missing += 1
    if not (bio_text and _LOCATION_RE.search(bio_text)):
        missing += 1
    if not (bio_text and (_has_contact(bio_text) or _has_booking_link(bio_text))):
        missing += 1
    if not (bio_text and _CTA_RE.search(bio_text)):
        missing += 1
    if not (isinstance(highlight_count, (int, float)) and highlight_count >= 1):
        missing += 1

    if missing <= 1:
        return 0
    if missing <= 3:
        return 1
    return 2


# --- category 4: limited reels / video content ------------------------------

def _score_reels_ratio(posts: Any, video_count: Any, image_count: Any) -> int:
    if isinstance(posts, list) and posts:
        total = len(posts)
        videos = sum(1 for p in posts if p.get("media_type") == "video")
    elif isinstance(posts, list):  # empty list
        return 2
    elif isinstance(video_count, (int, float)) and isinstance(image_count, (int, float)):
        total = video_count + image_count
        videos = video_count
    else:
        return 2  # insufficient data -> conservative fallback

    if total == 0:
        return 2
    ratio = videos / total
    if ratio >= 0.4:
        return 0
    if ratio >= 0.15:
        return 1
    return 2


# --- category 5: niche-specific content gaps --------------------------------

def _niche_checks(niche: str, bio: Any, captions: Any, highlight_names: Any) -> list[bool]:
    bio_text = bio if _text_available(bio) else ""
    caps = captions if isinstance(captions, list) else []
    cap_text = " ".join(c for c in caps if isinstance(c, str))
    names = highlight_names if isinstance(highlight_names, list) else []
    names_text = " ".join(n for n in names if isinstance(n, str)).lower()

    def in_highlight_or_bio(pattern: re.Pattern) -> bool:
        return bool(pattern.search(names_text)) or bool(bio_text and pattern.search(bio_text))

    if niche == "cafes/bakeries/home-food":
        return [
            in_highlight_or_bio(re.compile(r"menu", re.I)),
            bool(bio_text and _ORDER_DELIVERY_RE.search(bio_text)),
            bool(bio_text and _LOCATION_RE.search(bio_text)),
        ]
    if niche == "tutors/coaching":
        return [
            bool(bio_text and _SUBJECT_RE.search(bio_text)),
            bool(bio_text and (_has_contact(bio_text) or _ENROLLMENT_RE.search(bio_text))),
            bool(cap_text and _EDU_CAPTION_RE.search(cap_text)),
        ]
    if niche == "restaurants":
        return [
            bool(bio_text and (_has_booking_link(bio_text) or _ORDER_DELIVERY_RE.search(bio_text))),
            bool(bio_text and _LOCATION_RE.search(bio_text) and _HOURS_RE.search(bio_text)),
            bool(bio_text and (_RESERVATION_RE.search(bio_text) or _has_contact(bio_text))),
        ]
    if niche == "salons/spas/beauty":
        return [
            bool(bio_text and (_CTA_RE.search(bio_text) or _has_booking_link(bio_text))),
            bool(bio_text and _SERVICES_RE.search(bio_text)),
            bool(bio_text and _HOURS_RE.search(bio_text)),
        ]
    if niche == "boutiques/clothing/jewelry":
        return [
            bool(names_text and _SIZING_PRICING_DELIVERY_RE.search(names_text)),
            bool(bio_text and (_has_booking_link(bio_text) or _CATALOG_LINK_RE.search(bio_text))),
        ]
    if niche == "clinics/doctors/dentists/vets/pet-groomers":
        return [
            bool(bio_text and (_APPOINTMENT_RE.search(bio_text) or _has_contact(bio_text))),
            bool(bio_text and _SERVICES_RE.search(bio_text)),
            bool(bio_text and _HOURS_RE.search(bio_text)),
        ]
    return []


def _score_niche_specific(checks: list[bool]) -> int:
    if not checks:
        return 2
    missing = sum(1 for present in checks if not present)
    if missing == 0:
        return 0
    if missing == 1:
        return 1
    return 2


# --- category 6: missing conversion information -----------------------------

def _score_conversion_info(bio: Any) -> int:
    bio_text = bio if _text_available(bio) else ""
    checks = [
        bool(bio_text and _has_contact(bio_text)),
        bool(bio_text and (_has_booking_link(bio_text) or _ORDER_DELIVERY_RE.search(bio_text)
                            or _CTA_RE.search(bio_text))),
        bool(bio_text and _LOCATION_RE.search(bio_text)),
    ]
    present = sum(checks)
    if present == 3:
        return 0
    if present == 2:
        return 1
    return 2


# --- category 7: limited visible social proof -------------------------------

def _score_social_proof(bio: Any, captions: Any, highlight_names: Any) -> int:
    bio_text = bio if _text_available(bio) else ""
    names = highlight_names if isinstance(highlight_names, list) else []
    names_text = " ".join(n for n in names if isinstance(n, str)).lower()
    caps = captions if isinstance(captions, list) else None

    dedicated = bool(re.search(r"review|testimonial", names_text)) or bool(
        bio_text and _REVIEW_MENTION_RE.search(bio_text)
    )
    if dedicated:
        return 0
    if caps is not None and any(_CUSTOMER_MENTION_RE.search(c) for c in caps if isinstance(c, str)):
        return 1
    return 2


# --- data quality ------------------------------------------------------------

_CORE_FIELDS = (
    "bio", "follower_count", "following_count", "posts",
    "highlight_count", "video_count", "image_count",
)


def _data_quality(record: dict, captions: Any, highlight_names: Any) -> dict:
    unavailable_fields = [f for f in _CORE_FIELDS if record.get(f, UNAVAILABLE) == UNAVAILABLE]
    if captions == UNAVAILABLE:
        unavailable_fields.append("captions")
    if highlight_names == UNAVAILABLE:
        unavailable_fields.append("highlight_names")
    return {"unavailable_fields": unavailable_fields}


# --- public entry point ------------------------------------------------------

def analyze(record: dict, niche: str, now: datetime | None = None) -> dict:
    """
    Pure scoring function. `record` is shaped like collector.py's output
    (see module docstring for the two documented gaps this handles via
    graceful degradation). `niche` is one of NICHE_KEYS. `now` defaults to
    the current UTC time; pass explicitly for deterministic tests.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    bio = record.get("bio", UNAVAILABLE)
    follower_count = record.get("follower_count", UNAVAILABLE)
    posts = record.get("posts", UNAVAILABLE)
    highlight_count = record.get("highlight_count", UNAVAILABLE)
    video_count = record.get("video_count", UNAVAILABLE)
    image_count = record.get("image_count", UNAVAILABLE)
    captions = record.get("captions", UNAVAILABLE)
    highlight_names = record.get("highlight_names", UNAVAILABLE)

    recency = _recency(posts, now)
    tier = _follower_tier(follower_count)
    engagement = _engagement(posts, follower_count)

    score_1 = _score_posting_inactivity(posts, recency["days_since_last_post"], now)
    score_2 = _score_profile_optimization(bio, highlight_count)
    score_4 = _score_reels_ratio(posts, video_count, image_count)
    score_5 = _score_niche_specific(_niche_checks(niche, bio, captions, highlight_names))
    score_6 = _score_conversion_info(bio)
    score_7 = _score_social_proof(bio, captions, highlight_names)

    category_scores = {
        "posting_inactivity_or_inconsistency": score_1,
        "profile_optimization_gaps": score_2,
        "visual_content_quality_gaps": None,
        "limited_reels_or_video_content": score_4,
        "niche_specific_content_gaps": score_5,
        "missing_conversion_information": score_6,
        "limited_visible_social_proof": score_7,
        "brand_consistency_issues": None,
    }
    total_score = score_1 + score_2 + score_4 + score_5 + score_6 + score_7

    manual_review = [
        {"category": "visual_content_quality_gaps",
         "reason": "requires visual judgment (lighting/composition/image quality)"},
        {"category": "brand_consistency_issues",
         "reason": "requires visual judgment (brand/visual consistency)"},
    ]

    flags = ["needs_manual_review"]
    if recency["bucket"] == "90+":
        flags.append("verify_still_operating")
    if captions == UNAVAILABLE:
        flags.append("captions_unavailable")
    if highlight_names == UNAVAILABLE:
        flags.append("highlight_names_unavailable")

    profile_metrics = {
        "follower_count": follower_count,
        "follower_tier": tier,
        "last_post_recency_bucket": recency["bucket"],
        "days_since_last_post": recency["days_since_last_post"],
        "unavailable_reason": recency["unavailable_reason"],
        "engagement": engagement,
    }

    return {
        "handle": record.get("handle", UNAVAILABLE),
        "niche": niche,
        "profile_metrics": profile_metrics,
        "category_scores": category_scores,
        "total_score": total_score,
        "manual_review": manual_review,
        "flags": flags,
        "data_quality": _data_quality(record, captions, highlight_names),
    }

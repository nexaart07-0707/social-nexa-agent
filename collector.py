"""
Module 3 — Data Collector

Owns: pulling profile/post/highlight data for one Instagram handle.
Does NOT own: scoring (Module 4) or discovery/handle-guessing (Module 2).

ALWAYS UNAUTHENTICATED: this project no longer logs into Instagram at all
(the old session_manager.py / Module 1 login flow has been removed —
public/unauthenticated collection only, per the project's current
architecture). `get_anonymous_loader()` below builds a plain, logged-out
`instaloader.Instaloader()`; orchestrator.py constructs one loader this way
and reuses it for every handle in a run.

ASSUMPTION (documented per task instructions): Module 2 only produces a
fuzzy `handle_guess` for a business name — actually resolving that guess
into a confirmed real Instagram handle (e.g. via IG's in-app search) is out
of scope for this module. `collect_profile`/`collect_many` below take an
already-resolved/confirmed handle string as input; the guess -> confirmed
handle resolution step is left to a future module or manual step.

Data-quality note (no login): instaloader's anonymous
`Profile.from_username()` call works the same whether or not the loader is
logged in, but Instagram exposes less to logged-out requests — on
borderline-private or rate-limited accounts, or for certain post metadata,
some fields may come back inaccessible more often than a logged-in session
would see. This is NOT a new failure mode: it's handled by the same
"unavailable" marker system below as any other missing field, exactly as
before. This module doesn't attempt to enumerate precisely which fields are
affected — instaloader's anonymous-access limitations vary and aren't
precisely documented upstream either.

Data contract (Module 3 -> 4, per Architecture.md):
    {
        handle, bio, follower_count, following_count,
        posts: [{date, likes, comments, media_type}, ...],
        highlight_count, video_count, image_count,
    }
Any field the platform doesn't return, or that's private/blocked/errored,
is marked with the literal string "unavailable" — never `0` or `None`.
This is a hard rule from TRD.md and Scoring-Spec.md.

Media type note: instaloader's public `Post` object exposes `typename`
(GraphImage / GraphVideo / GraphSidecar) and `is_video`, but does not
reliably expose a separate "this GraphVideo is specifically a Reel" flag
without extra private/undocumented fields. Posts are therefore classified
as "image", "video", or "carousel"; Reels fall under "video". This matches
Scoring-Spec.md's Reels-ratio need, which itself treats "video_or_reel"
as one bucket (`video_count` here IS that bucket).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import instaloader
from instaloader.exceptions import (
    ConnectionException,
    InstaloaderException,
    TooManyRequestsException,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] collector: %(message)s",
)
logger = logging.getLogger("collector")

UNAVAILABLE = "unavailable"
MAX_POSTS = 15
RETRYABLE_EXCEPTIONS = (TooManyRequestsException, ConnectionException)


def get_anonymous_loader() -> instaloader.Instaloader:
    """Build a plain, logged-out Instaloader instance — no session file, no
    login. Mirrors the constructor kwargs the old session_manager.get_loader()
    used (download flags off; this project only reads profile/post metadata,
    never downloads media)."""
    return instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
    )


def _empty_result(handle: str, error: str | None = None) -> dict[str, Any]:
    result = {
        "handle": handle,
        "bio": UNAVAILABLE,
        "follower_count": UNAVAILABLE,
        "following_count": UNAVAILABLE,
        "posts": UNAVAILABLE,
        "highlight_count": UNAVAILABLE,
        "video_count": UNAVAILABLE,
        "image_count": UNAVAILABLE,
    }
    if error:
        result["error"] = error
    return result


def _with_backoff(func, max_retries: int = 4, base_delay: float = 5.0):
    """
    Run func() with exponential backoff on rate-limit / connection errors.
    Re-raises the last exception (or any non-retryable one) to the caller.
    """
    for attempt in range(max_retries):
        try:
            return func()
        except RETRYABLE_EXCEPTIONS as exc:
            if attempt == max_retries - 1:
                raise
            wait = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            logger.warning(
                "Rate-limited/connection issue (%s); backing off %.1fs (attempt %d/%d).",
                exc, wait, attempt + 1, max_retries,
            )
            time.sleep(wait)


def _media_type(post) -> str:
    if getattr(post, "typename", None) == "GraphSidecar":
        return "carousel"
    return "video" if post.is_video else "image"


def collect_profile(loader: instaloader.Instaloader, handle: str) -> dict[str, Any]:
    """
    Pull bio, follower/following counts, last MAX_POSTS posts, highlight
    count, and video/image counts for one handle. Never raises — any
    failure (nonexistent handle, private account, rate limit exhausted,
    unexpected error) results in a dict with the affected fields marked
    "unavailable" (plus an "error" key describing what happened), so one
    handle's failure never breaks collection of the others.
    """
    try:
        profile = _with_backoff(
            lambda: instaloader.Profile.from_username(loader.context, handle)
        )
    except InstaloaderException as exc:
        logger.warning("Could not load profile for %r: %s", handle, exc)
        return _empty_result(handle, error=f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - failure isolation, never crash
        logger.exception("Unexpected error loading profile for %r", handle)
        return _empty_result(handle, error=f"unexpected error: {exc}")

    result: dict[str, Any] = {"handle": handle}

    try:
        result["bio"] = profile.biography if profile.biography is not None else UNAVAILABLE
    except Exception as exc:  # noqa: BLE001
        logger.warning("bio unavailable for %r: %s", handle, exc)
        result["bio"] = UNAVAILABLE

    try:
        result["follower_count"] = profile.followers
    except Exception as exc:  # noqa: BLE001
        logger.warning("follower_count unavailable for %r: %s", handle, exc)
        result["follower_count"] = UNAVAILABLE

    try:
        result["following_count"] = profile.followees
    except Exception as exc:  # noqa: BLE001
        logger.warning("following_count unavailable for %r: %s", handle, exc)
        result["following_count"] = UNAVAILABLE

    try:
        posts = []
        for post in _with_backoff(lambda: profile.get_posts()):
            if len(posts) >= MAX_POSTS:
                break

            try:
                date = post.date_utc.isoformat()
            except Exception:  # noqa: BLE001
                date = UNAVAILABLE
            try:
                likes = post.likes
            except Exception:  # noqa: BLE001
                likes = UNAVAILABLE
            try:
                comments = post.comments
            except Exception:  # noqa: BLE001
                comments = UNAVAILABLE
            try:
                media_type = _media_type(post)
            except Exception:  # noqa: BLE001
                media_type = UNAVAILABLE

            posts.append(
                {"date": date, "likes": likes, "comments": comments, "media_type": media_type}
            )
        result["posts"] = posts
    except InstaloaderException as exc:
        # e.g. private account not followed -> posts inaccessible.
        logger.warning("posts unavailable for %r: %s", handle, exc)
        result["posts"] = UNAVAILABLE
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error collecting posts for %r", handle)
        result["posts"] = UNAVAILABLE

    if isinstance(result["posts"], list):
        result["video_count"] = sum(1 for p in result["posts"] if p["media_type"] == "video")
        result["image_count"] = sum(
            1 for p in result["posts"] if p["media_type"] in ("image", "carousel")
        )
    else:
        result["video_count"] = UNAVAILABLE
        result["image_count"] = UNAVAILABLE

    try:
        result["highlight_count"] = sum(1 for _ in loader.get_highlights(profile))
    except Exception as exc:  # noqa: BLE001
        logger.warning("highlight_count unavailable for %r: %s", handle, exc)
        result["highlight_count"] = UNAVAILABLE

    return result


def collect_many(
    loader: instaloader.Instaloader,
    handles: list[str],
    delay_range: tuple[float, float] = (3.0, 8.0),
) -> list[dict[str, Any]]:
    """
    Collect profiles for multiple handles, pacing requests with a randomized
    delay between each. One handle's total failure never stops the others
    (collect_profile already isolates per-field failures; this loop also
    guards the call itself in case of a truly unexpected error).
    """
    results = []
    for i, handle in enumerate(handles):
        try:
            results.append(collect_profile(loader, handle))
        except Exception as exc:  # noqa: BLE001 - absolute last-resort isolation
            logger.exception("collect_profile crashed unexpectedly for %r", handle)
            results.append(_empty_result(handle, error=f"unexpected crash: {exc}"))

        if i < len(handles) - 1:
            time.sleep(random.uniform(*delay_range))

    return results

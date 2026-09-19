"""
Module 1 — Session Manager (Instagram)

Owns: Instagram login/session persistence, expiry/challenge detection.
Does NOT own: scraping business data itself (that's Module 3 / collector.py).

Usage:
    python session_manager.py                 # load or create session, exit 0/1/2/3
    python session_manager.py --dry-run        # exercise the load-vs-login branch
                                                # with a fake in-memory backend,
                                                # no real Instagram/network calls

Config (env vars):
    IG_USERNAME        Instagram username (required)
    IG_PASSWORD        Instagram password (required only for first-time login;
                        not needed once a session file exists)
    IG_SESSION_DIR     Directory to store the session file (default: ./session)
    IG_SESSION_FILE    Full path override for the session file (optional;
                        defaults to "<IG_SESSION_DIR>/<IG_USERNAME>.session")

Failure handling (per TRD.md Module 1):
    This module detects two conditions and HALTS (non-zero exit) without any
    retry and without attempting to auto-solve anything:
      1. SessionInvalidError  — the saved session is rejected/expired.
         Detected the way instaloader's own CLI (__main__.py) detects it:
         after load_session_from_file(), call test_login() and compare the
         returned username — no string-matching of error text.
      2. ChallengeRequiredError — Instagram demands a checkpoint/verification
         challenge during login. instaloader has a dedicated
         TwoFactorAuthRequiredException for 2FA challenges; a checkpoint
         challenge (no dedicated subclass in current instaloader) surfaces as
         a plain LoginException that is not a wrong-password
         (BadCredentialsException) case — we classify by exception type, not
         by inspecting the message text.
    Both cases call notify() (a stub today — Module 5 will point this at the
    real Telegram Bot API later) and cause main() to return non-zero.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import instaloader
from instaloader.exceptions import (
    BadCredentialsException,
    LoginException,
    TwoFactorAuthRequiredException,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] session_manager: %(message)s",
)
logger = logging.getLogger("session_manager")


class SessionManagerError(Exception):
    """Base error for conditions that must halt automation (no retry)."""


class SessionInvalidError(SessionManagerError):
    """Raised when a saved session file exists but Instagram rejects it."""


class ChallengeRequiredError(SessionManagerError):
    """Raised when Instagram demands a checkpoint/verification challenge."""


def notify(message: str) -> None:
    """
    Stub notification hook — extension point for Module 5.

    Module 5 (Storage & Notification) will replace/wrap this with a real
    Telegram Bot API call. For now it just logs clearly so a human operator
    watching logs/console sees it immediately.
    """
    logger.warning("NOTIFY: %s", message)


def default_session_path(username: str) -> Path:
    override = os.environ.get("IG_SESSION_FILE")
    if override:
        return Path(override)
    session_dir = Path(os.environ.get("IG_SESSION_DIR", "./session"))
    return session_dir / f"{username}.session"


def get_loader() -> instaloader.Instaloader:
    """Construct the Instaloader instance used for both login and load paths."""
    return instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
    )


def login_and_save(loader, username: str, password: str, session_path: Path) -> None:
    """One-time interactive login path: authenticate and persist the session."""
    logger.info("No saved session found for %s; performing interactive login.", username)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        loader.login(username, password)
    except TwoFactorAuthRequiredException as exc:
        message = f"Instagram two-factor challenge required for {username}: {exc}"
        logger.error(message)
        notify(message)
        raise ChallengeRequiredError(message) from exc
    except BadCredentialsException as exc:
        # Wrong password: a config problem, not one of the two TRD halt
        # states, but still must not retry/auto-solve.
        raise SessionManagerError(f"Instagram rejected the password for {username}: {exc}") from exc
    except LoginException as exc:
        # Any other LoginException (e.g. checkpoint_url required) — classified
        # by exception type, not by parsing the message string.
        message = f"Instagram checkpoint/verification challenge required for {username}: {exc}"
        logger.error(message)
        notify(message)
        raise ChallengeRequiredError(message) from exc

    loader.save_session_to_file(str(session_path))
    logger.info("Session saved to %s", session_path)


def load_saved_session(loader, username: str, session_path: Path) -> None:
    """
    Load-existing-session path: no login prompt, no re-entering credentials.

    Validates the loaded session using instaloader's own documented pattern
    (see instaloader/__main__.py): call test_login() after loading and
    confirm it returns the expected username. Raises SessionInvalidError if
    the saved session is rejected/expired.
    """
    logger.info("Loading saved session for %s from %s", username, session_path)
    loader.load_session_from_file(username, str(session_path))

    validated_username = loader.test_login()
    if not validated_username or validated_username != username:
        raise SessionInvalidError(
            f"Saved session for {username} is invalid or expired (test_login() "
            f"returned {validated_username!r})."
        )
    logger.info("Saved session for %s is valid.", username)


def ensure_session(username: str | None = None, password: str | None = None) -> Path:
    """
    Top-level entry point (also usable by orchestrator.py later).

    Returns the path to a valid, loaded session file on success.
    Raises SessionInvalidError / ChallengeRequiredError / SessionManagerError
    on failure — callers decide how to turn that into a process exit code
    (see main() below).
    """
    username = username or os.environ.get("IG_USERNAME")
    password = password or os.environ.get("IG_PASSWORD")
    if not username:
        raise SessionManagerError("IG_USERNAME is not set and no username was provided.")

    session_path = default_session_path(username)
    loader = get_loader()

    if session_path.exists():
        load_saved_session(loader, username, session_path)
    else:
        if not password:
            raise SessionManagerError(
                "No saved session and IG_PASSWORD is not set; cannot perform "
                "one-time interactive login."
            )
        login_and_save(loader, username, password, session_path)

    return session_path


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    if "--dry-run" in argv:
        return _dry_run()

    try:
        session_path = ensure_session()
    except SessionInvalidError as exc:
        notify(str(exc))
        logger.error("Halting: saved session invalid/expired. %s", exc)
        return 2
    except ChallengeRequiredError as exc:
        logger.error("Halting: Instagram checkpoint/verification challenge. %s", exc)
        return 3
    except SessionManagerError as exc:
        logger.error("Halting: %s", exc)
        return 1

    logger.info("Session ready at %s", session_path)
    return 0


def _dry_run() -> int:
    """
    Demonstrates the load-vs-login branching end to end with a fake,
    in-memory instaloader-like backend — no real network calls, no real
    instaloader session validation. Lets the manager verify Module 1's DoD
    ("second run reuses saved session, no re-login prompt") without real
    Instagram credentials. See test_session_manager.py for the same guarantee
    expressed as pytest assertions against the real functions with mocks.
    """
    import tempfile

    calls = {"login": 0, "load": 0}

    class FakeLoader:
        def login(self, username, password):
            calls["login"] += 1

        def save_session_to_file(self, path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("fake-session")

        def load_session_from_file(self, username, path):
            calls["load"] += 1

        def test_login(self):
            return "demo_user"

    with tempfile.TemporaryDirectory() as tmp:
        session_path = Path(tmp) / "demo_user.session"

        for run_number in (1, 2):
            loader = FakeLoader()
            if session_path.exists():
                load_saved_session(loader, "demo_user", session_path)
            else:
                login_and_save(loader, "demo_user", "demo_pass", session_path)
            logger.info(
                "Dry-run pass %d complete (cumulative login=%d, load=%d)",
                run_number, calls["login"], calls["load"],
            )

    if calls["login"] == 1 and calls["load"] == 1:
        logger.info("PASS: run 1 logged in and saved; run 2 loaded the saved session with no re-login.")
        return 0
    logger.error("FAIL: expected exactly 1 login and 1 load, got %s", calls)
    return 1


if __name__ == "__main__":
    sys.exit(main())

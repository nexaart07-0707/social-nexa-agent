"""
Tests for Module 1 (Session Manager).

Run with:
    py -3 -m pytest test_session_manager.py -v

All instaloader interaction is mocked (unittest.mock) — no real network
calls, no real Instagram account needed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from instaloader.exceptions import TwoFactorAuthRequiredException, LoginException

import session_manager as sm


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point session storage at a throwaway temp dir for every test."""
    monkeypatch.setenv("IG_SESSION_DIR", str(tmp_path / "session"))
    monkeypatch.delenv("IG_SESSION_FILE", raising=False)
    monkeypatch.setenv("IG_USERNAME", "test_user")
    monkeypatch.setenv("IG_PASSWORD", "test_pass")
    yield tmp_path


def test_no_session_file_triggers_login_path(monkeypatch, isolated_env):
    """When no session file exists, login() must be invoked and the session saved."""
    fake_loader = MagicMock()
    fake_loader.test_login.return_value = "test_user"
    monkeypatch.setattr(sm, "get_loader", lambda: fake_loader)

    session_path = sm.ensure_session("test_user", "test_pass")

    fake_loader.login.assert_called_once_with("test_user", "test_pass")
    fake_loader.save_session_to_file.assert_called_once_with(str(session_path))
    fake_loader.load_session_from_file.assert_not_called()


def test_existing_session_file_is_loaded_without_login(monkeypatch, isolated_env):
    """When a session file exists, it's loaded directly — login() is NOT invoked."""
    session_path = sm.default_session_path("test_user")
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("pretend-this-is-a-real-session")

    fake_loader = MagicMock()
    fake_loader.test_login.return_value = "test_user"
    monkeypatch.setattr(sm, "get_loader", lambda: fake_loader)

    result_path = sm.ensure_session("test_user", "test_pass")

    fake_loader.load_session_from_file.assert_called_once_with("test_user", str(session_path))
    fake_loader.login.assert_not_called()
    assert result_path == session_path


def test_two_runs_second_reuses_session_no_relogin(monkeypatch, isolated_env):
    """
    End-to-end demonstration of the module's DoD: run ensure_session() twice
    against the same (mocked) backend/session dir. First run logs in and
    saves; second run loads the saved file and never calls login() again.
    """
    fake_loader_1 = MagicMock()
    fake_loader_1.test_login.return_value = "test_user"

    def fake_save(path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("session-data")

    fake_loader_1.save_session_to_file.side_effect = fake_save

    fake_loader_2 = MagicMock()
    fake_loader_2.test_login.return_value = "test_user"

    loaders = iter([fake_loader_1, fake_loader_2])
    monkeypatch.setattr(sm, "get_loader", lambda: next(loaders))

    sm.ensure_session("test_user", "test_pass")
    fake_loader_1.login.assert_called_once()

    sm.ensure_session("test_user", "test_pass")
    fake_loader_2.login.assert_not_called()
    fake_loader_2.load_session_from_file.assert_called_once()


def test_invalid_saved_session_calls_notify_and_reports_failure(monkeypatch, isolated_env):
    """
    Saved session exists but instaloader's test_login() indicates it is no
    longer valid (returns None, exactly as instaloader's own CLI checks it).
    main() must call notify() and return a non-zero status, without retrying.
    """
    session_path = sm.default_session_path("test_user")
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("stale-session")

    fake_loader = MagicMock()
    fake_loader.test_login.return_value = None
    monkeypatch.setattr(sm, "get_loader", lambda: fake_loader)

    notify_calls = []
    monkeypatch.setattr(sm, "notify", lambda msg: notify_calls.append(msg))

    with pytest.raises(sm.SessionInvalidError):
        sm.ensure_session("test_user", "test_pass")

    exit_code = sm.main([])
    assert exit_code == 2
    assert len(notify_calls) == 1
    assert "test_user" in notify_calls[0]
    fake_loader.login.assert_not_called()


def test_challenge_required_during_login_calls_notify_and_reports_failure(monkeypatch, isolated_env):
    """
    No saved session -> login path runs -> instaloader raises
    TwoFactorAuthRequiredException (a real, specific instaloader exception,
    not a guessed string match). main() must call notify() and return a
    non-zero status, without retrying or attempting to solve the challenge.
    """
    fake_loader = MagicMock()
    fake_loader.login.side_effect = TwoFactorAuthRequiredException("2FA required")
    monkeypatch.setattr(sm, "get_loader", lambda: fake_loader)

    notify_calls = []
    monkeypatch.setattr(sm, "notify", lambda msg: notify_calls.append(msg))

    exit_code = sm.main([])
    assert exit_code == 3
    assert len(notify_calls) == 1
    fake_loader.save_session_to_file.assert_not_called()


def test_checkpoint_login_exception_also_treated_as_challenge(monkeypatch, isolated_env):
    """A plain LoginException (checkpoint_url case) that isn't bad-credentials
    is also classified as a challenge requiring human action, not retried."""
    fake_loader = MagicMock()
    fake_loader.login.side_effect = LoginException("Checkpoint required. Point your browser to ...")
    monkeypatch.setattr(sm, "get_loader", lambda: fake_loader)

    notify_calls = []
    monkeypatch.setattr(sm, "notify", lambda msg: notify_calls.append(msg))

    with pytest.raises(sm.ChallengeRequiredError):
        sm.ensure_session("test_user", "test_pass")

    assert len(notify_calls) == 1


def test_dry_run_passes():
    """The built-in --dry-run demonstration (fake backend) is itself correct."""
    assert sm.main(["--dry-run"]) == 0

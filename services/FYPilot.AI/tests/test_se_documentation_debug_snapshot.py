"""
Tests for app/review/se_documentation_debug_snapshot.py -- the opt-in,
disabled-by-default local candidate snapshot used to replay a failed live
SE Documentation run's candidate offline, with zero provider calls.
"""

from __future__ import annotations

import json
import os

from app.review.se_documentation_debug_snapshot import maybe_write_se_documentation_debug_snapshot


def test_disabled_by_default_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", raising=False)

    maybe_write_se_documentation_debug_snapshot({"projectTitle": "X"}, request_id="req-1")

    assert list(tmp_path.iterdir()) == []


def test_enabled_writes_one_file_named_by_request_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(tmp_path))

    maybe_write_se_documentation_debug_snapshot({"projectTitle": "X"}, request_id="req-abc-123")

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert "req-abc-123" in files[0].name
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["projectTitle"] == "X"


def test_rewriting_the_same_request_id_overwrites_not_accumulates(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(tmp_path))

    maybe_write_se_documentation_debug_snapshot({"projectTitle": "First"}, request_id="req-1")
    maybe_write_se_documentation_debug_snapshot({"projectTitle": "Second"}, request_id="req-1")

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["projectTitle"] == "Second"


def test_provider_api_key_values_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-super-secret-value-123456")

    candidate = {"projectTitle": "sk-ant-super-secret-value-123456 leaked into content"}
    maybe_write_se_documentation_debug_snapshot(candidate, request_id="req-1")

    text = tmp_path.joinpath(next(tmp_path.iterdir()).name).read_text(encoding="utf-8")
    assert "sk-ant-super-secret-value-123456" not in text
    assert "[REDACTED]" in text


def test_generic_key_shaped_string_is_redacted_even_without_a_configured_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    candidate = {"note": "token=" + "a" * 40}
    maybe_write_se_documentation_debug_snapshot(candidate, request_id="req-1")

    text = tmp_path.joinpath(next(tmp_path.iterdir()).name).read_text(encoding="utf-8")
    assert "a" * 40 not in text
    assert "[REDACTED]" in text


def test_short_values_under_sensitive_keys_and_inline_assignments_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(tmp_path))

    candidate = {
        "password": "tiny",
        "nested": {"access_token": "abc123"},
        "note": "password=short-secret",
        "authenticationFlow": "preserved non-secret project text",
    }
    maybe_write_se_documentation_debug_snapshot(candidate, request_id="req-sensitive")

    data = json.loads(next(tmp_path.iterdir()).read_text(encoding="utf-8"))
    assert data["password"] == "[REDACTED]"
    assert data["nested"]["access_token"] == "[REDACTED]"
    assert data["note"] == "password=[REDACTED]"
    assert data["authenticationFlow"] == "preserved non-secret project text"


def test_oversized_snapshot_is_bounded_not_unbounded(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(tmp_path))

    huge_candidate = {"filler": "x" * 3_000_000}
    maybe_write_se_documentation_debug_snapshot(huge_candidate, request_id="req-1")

    path = tmp_path.joinpath(next(tmp_path.iterdir()).name)
    assert path.stat().st_size <= 2_100_000  # bound + small truncation-message overhead


def test_snapshot_retention_keeps_only_the_newest_twenty_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(tmp_path))

    for index in range(25):
        maybe_write_se_documentation_debug_snapshot(
            {"projectTitle": f"Project {index}"},
            request_id=f"req-{index:02d}",
        )

    files = sorted(tmp_path.glob("se_documentation_candidate_*.json"))
    assert len(files) == 20
    assert not (tmp_path / "se_documentation_candidate_req-00.json").exists()
    assert (tmp_path / "se_documentation_candidate_req-24.json").exists()


def test_request_id_is_sanitized_for_the_filesystem(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(tmp_path))

    maybe_write_se_documentation_debug_snapshot({"projectTitle": "X"}, request_id="../../etc/passwd")

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert os.path.dirname(str(files[0])) == str(tmp_path)


def test_write_failure_never_raises(tmp_path, monkeypatch):
    # Points the "directory" at an existing FILE, so os.makedirs/open both
    # fail -- must not raise; a snapshot failure must never affect the
    # pipeline result.
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("x")
    monkeypatch.setenv("SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR", str(blocking_file))

    maybe_write_se_documentation_debug_snapshot({"projectTitle": "X"}, request_id="req-1")

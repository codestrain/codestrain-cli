"""Integration tests for JSONL discovery + parsing.

Uses synthetic fixtures under cli/tests/fixtures/projects/ so tests pass
without needing the user's ~/.claude/projects/. Run as:

    cd cli && python -m pytest tests/test_jsonl.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_find_jsonl_discovers_all(cli, projects_dir: Path):
    # find_jsonl_files returns a list of (project_name, jsonl_path) tuples.
    results = cli.find_jsonl_files(str(projects_dir))
    names = sorted(Path(p).name for _proj, p in results)
    # 4 fixture JSONL files total (3 sessions + 1 empty)
    assert names == ["empty.jsonl", "session-001.jsonl", "session-002.jsonl", "session-003.jsonl"]


def test_find_jsonl_project_filter(cli, projects_dir: Path):
    results = cli.find_jsonl_files(str(projects_dir), project_filter="projectA")
    names = sorted(Path(p).name for _proj, p in results)
    assert names == ["session-001.jsonl", "session-002.jsonl"]


def test_find_jsonl_returns_empty_for_unknown_filter(cli, projects_dir: Path):
    files = cli.find_jsonl_files(str(projects_dir), project_filter="does-not-exist-anywhere")
    assert files == []


def test_parse_jsonl_clean_session(cli, projects_dir: Path):
    f = projects_dir / "-Users-test-projectA" / "session-001.jsonl"
    events = cli.parse_jsonl(str(f))
    # 6 events in the fixture (3 user + 3 assistant)
    assert len(events) == 6
    # First and last events have expected types
    assert events[0]["type"] == "user"
    assert events[-1]["type"] == "assistant"


def test_parse_jsonl_skips_malformed_line(cli, projects_dir: Path):
    f = projects_dir / "-Users-test-projectA" / "session-002.jsonl"
    events = cli.parse_jsonl(str(f))
    # 4 valid events; 1 deliberately broken line in the middle that must be skipped.
    assert len(events) == 4
    # All survivors are valid dicts
    assert all(isinstance(e, dict) for e in events)


def test_parse_jsonl_empty_file_returns_empty(cli, projects_dir: Path):
    f = projects_dir / "empty-project" / "empty.jsonl"
    events = cli.parse_jsonl(str(f))
    assert events == []


def test_extract_session_stats_aggregates_tokens_and_cost(cli, projects_dir: Path):
    f = projects_dir / "-Users-test-projectA" / "session-001.jsonl"
    events = cli.parse_jsonl(str(f))
    stats = cli.extract_session_stats(events)
    # Sums from fixture session-001.jsonl:
    # input  = 1200 + 800 + 600 = 2600
    # output = 450 + 220 + 80   = 750
    # cost   = 0.05 + 0.02 + 0.01 = 0.08
    assert stats["total_input_tokens"] == 2600
    assert stats["total_output_tokens"] == 750
    assert stats["total_cost"] == pytest.approx(0.08, abs=1e-9)
    # 6 turn-eligible events
    assert stats["turn_count"] == 6
    assert stats["duration_seconds"] > 0
    assert "claude-opus-4-7" in stats["models"]


def test_extract_session_stats_counts_error_turns(cli, projects_dir: Path):
    # session-003 contains multiple "Error/Exception/traceback" user turns
    f = projects_dir / "-Users-test-projectB" / "session-003.jsonl"
    events = cli.parse_jsonl(str(f))
    stats = cli.extract_session_stats(events)
    # At least 3 user turns contain error-like keywords
    assert stats["error_turns"] >= 3


def test_extract_session_stats_empty_input(cli):
    stats = cli.extract_session_stats([])
    assert stats["turn_count"] == 0
    assert stats["total_input_tokens"] == 0
    assert stats["total_output_tokens"] == 0
    assert stats["total_cost"] == 0.0
    assert stats["error_turns"] == 0
    assert stats["duration_seconds"] == 0.0
    assert stats["models"] == set()


def test_main_runs_against_fixtures(cli, projects_dir: Path, monkeypatch, capsys):
    """Smoke: invoking main() with --path pointing at fixtures must not raise.

    We monkey-patch sys.argv rather than the lower-level parser to exercise
    the same path a user would hit.
    """
    monkeypatch.setattr(
        "sys.argv",
        ["codestrain_cli.py", "--path", str(projects_dir), "--no-color", "--all"],
    )
    try:
        cli.main()
    except SystemExit as exc:
        # argparse may raise SystemExit on --help-like exits; require exit code 0
        assert exc.code in (None, 0)
    out = capsys.readouterr().out
    # Header is drawn as ASCII art so the literal "CodeStrain" is not present;
    # the tagline + a recognizable section label are.
    assert "Your AI coding recovery score" in out
    assert "DRS Estimate" in out
    assert "Per-Project Breakdown" in out

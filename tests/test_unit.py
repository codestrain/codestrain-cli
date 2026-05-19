"""Pure-function unit tests for codestrain_cli.

No I/O, no JSONL — these run in < 100 ms total. Run as:

    cd cli && python -m pytest tests/test_unit.py -v
"""

from __future__ import annotations

import os

import pytest


# ─── Colors ─────────────────────────────────────────────────────────────────

def test_colors_off_when_no_color_env(cli, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli.Colors.enabled() is False


def test_colors_off_when_dumb_term(cli, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert cli.Colors.enabled() is False


def test_c_wraps_when_colors_on(cli, monkeypatch):
    monkeypatch.setattr(cli, "_colors_on", True)
    out = cli.c(cli.Colors.GREEN, "hi")
    assert out.startswith("\033[32m") and out.endswith("\033[0m") and "hi" in out


def test_c_bare_when_colors_off(cli, monkeypatch):
    monkeypatch.setattr(cli, "_colors_on", False)
    assert cli.c(cli.Colors.GREEN, "hi") == "hi"


# ─── DRS color + label tiers ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "recovery,want",
    [
        (100, "GREEN"), (80, "GREEN"), (67, "GREEN"),
        (66, "YELLOW"), (50, "YELLOW"), (34, "YELLOW"),
        (33, "RED"), (10, "RED"), (0, "RED"),
    ],
)
def test_drs_color_tiers(cli, monkeypatch, recovery, want):
    monkeypatch.setattr(cli, "_colors_on", False)
    label = cli.readiness_label(recovery)
    assert want in label


def test_drs_color_returns_ansi(cli):
    # Returns the raw color constant regardless of _colors_on
    assert cli.drs_color(80) == cli.Colors.GREEN
    assert cli.drs_color(50) == cli.Colors.YELLOW
    assert cli.drs_color(10) == cli.Colors.RED


# ─── Strain + recovery estimators ───────────────────────────────────────────

def test_strain_zero_hours_yields_zero(cli):
    assert cli.estimate_strain(0, 0) == pytest.approx(0.0, abs=0.1)


def test_strain_increases_with_hours(cli):
    a = cli.estimate_strain(2, 0)
    b = cli.estimate_strain(8, 0)
    assert b > a


def test_strain_late_night_penalty(cli):
    day = cli.estimate_strain(4, 0, is_late_night=False)
    night = cli.estimate_strain(4, 0, is_late_night=True)
    assert night > day


def test_strain_weekend_penalty(cli):
    weekday = cli.estimate_strain(4, 0, is_weekend=False)
    weekend = cli.estimate_strain(4, 0, is_weekend=True)
    assert weekend > weekday


def test_strain_clamped_at_21(cli):
    # 24h coding on a late-night weekend with debug spiral
    extreme = cli.estimate_strain(24, 1.0, is_late_night=True, is_weekend=True)
    assert 0 <= extreme <= 21


def test_recovery_inverse_to_strain(cli):
    a = cli.estimate_recovery(0, hours_since_last=12)
    b = cli.estimate_recovery(15, hours_since_last=12)
    assert a > b
    assert 0 <= a <= 100
    assert 0 <= b <= 100


def test_recovery_short_sleep_penalty(cli):
    # Same strain, short sleep should yield lower recovery than long sleep.
    rested = cli.estimate_recovery(10, hours_since_last=12)
    tired = cli.estimate_recovery(10, hours_since_last=2)
    assert rested >= tired


# ─── Formatters ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "seconds,expected_substr",
    [
        (0, "0"),
        (45, "45"),
        (90, "1m"),
        (3600, "1h"),
        (3661, "1h"),
    ],
)
def test_format_duration_includes_marker(cli, seconds, expected_substr):
    assert expected_substr in cli.format_duration(seconds)


@pytest.mark.parametrize(
    "cost,expected_prefix",
    [
        (0, "$0"),
        (0.05, "$0.05"),
        (1.23, "$1.23"),
        (10.5, "$10.50"),
    ],
)
def test_format_cost_dollars(cli, cost, expected_prefix):
    assert cli.format_cost(cost).startswith(expected_prefix)


@pytest.mark.parametrize(
    "n,expected_substr",
    [
        (0, "0"),
        (999, "999"),
        (1_500, "1.5"),
        (1_500_000, "1.5"),
    ],
)
def test_format_tokens_compact(cli, n, expected_substr):
    out = cli.format_tokens(n)
    assert expected_substr in out

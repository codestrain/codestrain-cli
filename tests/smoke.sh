#!/usr/bin/env bash
# CodeStrain CLI surface smoke tests.
#
# Drives every flag against the synthetic fixture set under
# `tests/fixtures/projects/`. No pip deps. Exits non-zero on first mismatch.
#
# Run from cli/ dir (or any dir — paths are anchored to this script):
#   cli/tests/smoke.sh
#
set -eu

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CLI_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
CLI="$CLI_DIR/codestrain_cli.py"
FIXTURES="$SCRIPT_DIR/fixtures/projects"

# Prefer the local venv python (set up via `uv venv` in cli/) — falls back
# to system python3 if the venv isn't present.
if [ -x "$CLI_DIR/.venv/bin/python" ]; then
    PY="$CLI_DIR/.venv/bin/python"
else
    PY=python3
fi

pass=0
fail=0

# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

ok() { printf "  \033[32m✓\033[0m %s\n" "$1"; pass=$((pass + 1)); }
ng() { printf "  \033[31m✗\033[0m %s\n     %s\n" "$1" "$2"; fail=$((fail + 1)); }

contains() {
    # contains "<haystack>" "<needle>" "<label>"
    if printf '%s' "$1" | grep -qF -- "$2"; then
        ok "$3"
    else
        ng "$3" "expected substring: '$2'"
    fi
}

not_contains() {
    if printf '%s' "$1" | grep -qF -- "$2"; then
        ng "$3" "must NOT contain: '$2'"
    else
        ok "$3"
    fi
}

# ----------------------------------------------------------------------------
# tests
# ----------------------------------------------------------------------------

printf "running CLI smoke tests against fixtures…\n\n"

# 1. --help exits 0 and advertises every flag
out=$("$PY" "$CLI" --help 2>&1) || { ng "--help" "exited non-zero"; out=""; }
contains "$out" "--all"      "--help mentions --all"
contains "$out" "--project"  "--help mentions --project"
contains "$out" "--path"     "--help mentions --path"
contains "$out" "--no-color" "--help mentions --no-color"

# 2. Default run (today's window) against fixtures, no color.
# Fixtures ship with a fixed timestamp, so on most days the auto-fallback
# kicks in and we render the All-Time view with a hint. Either outcome is
# valid — what matters is that we always reach a useful section.
out=$("$PY" "$CLI" --path "$FIXTURES" --no-color 2>&1)
if printf '%s' "$out" | grep -qE 'Today|All Time'; then
    ok "default run renders Today or All Time section"
else
    ng "default run renders Today or All Time section" "neither header found"
fi
# On any day other than the fixtures' date, the fallback hint must appear.
contains "$out" "showing all-time stats" "auto-fallback hint when today is empty"

# 3. --all aggregates everything and always has enough data for DRS.
out=$("$PY" "$CLI" --path "$FIXTURES" --all --no-color 2>&1)
contains "$out" "All Time" "--all shows 'All Time' label"
contains "$out" "Per-Project Breakdown" "--all shows per-project breakdown"
contains "$out" "DRS Estimate" "--all shows DRS Estimate"

# 4. --project filter keeps projectA, drops projectB.
out=$("$PY" "$CLI" --path "$FIXTURES" --all --project projectA --no-color 2>&1)
contains "$out" "projectA" "project filter keeps projectA"
not_contains "$out" "projectB" "project filter drops projectB"

# 5. NO_COLOR env strips ANSI sequences.
out=$(NO_COLOR=1 "$PY" "$CLI" --path "$FIXTURES" --all 2>&1)
if printf '%s' "$out" | grep -qE $'\033\[[0-9;]*m'; then
    ng "NO_COLOR strips ANSI" "found ANSI escapes in output"
else
    ok "NO_COLOR strips ANSI"
fi

# 6. Missing --path → graceful exit (0 or 1, but no Python traceback).
out=$("$PY" "$CLI" --path /tmp/does-not-exist-codestrain --no-color 2>&1) && rc=0 || rc=$?
if [ "$rc" -le 1 ]; then
    ok "missing path exits gracefully (rc=$rc)"
else
    ng "missing path graceful exit" "rc=$rc"
fi
not_contains "$out" "Traceback" "missing path: no Python traceback"

# 7. Empty project dir → still graceful.
empty=$(mktemp -d)
trap 'rm -rf "$empty"' EXIT
out=$("$PY" "$CLI" --path "$empty" --no-color 2>&1) && rc=0 || rc=$?
[ "$rc" -le 1 ] && ok "empty dir exits gracefully (rc=$rc)" || ng "empty dir" "rc=$rc"
not_contains "$out" "Traceback" "empty dir: no Python traceback"

# 8. Sanity: a known token count appears for projectA in --all mode.
out=$("$PY" "$CLI" --path "$FIXTURES" --all --project projectA --no-color 2>&1)
# session-001 has input 2600 + session-002 has input 1200 = 3800
# format_tokens compacts to "3.8K"-style — assert the K
contains "$out" "3.8K" "expected ~3800 input tokens for projectA (compact = 3.8K)"

# 9. --share emits a codestrain.dev/s/ URL and implies --anonymize.
out=$("$PY" "$CLI" --path "$FIXTURES" --all --share 2>&1)
contains "$out" "https://codestrain.dev/s/?d=" "--share prints shareable URL"
contains "$out" "project-1"                    "--share implies --anonymize (project-1)"
not_contains "$out" "projectA"                 "--share scrubs real project names"
not_contains "$out" "projectB"                 "--share scrubs real project names"

# 10. --json emits a parseable document with the expected schema.
out=$("$PY" "$CLI" --path "$FIXTURES" --all --json 2>&1)
if printf '%s' "$out" | "$PY" -c 'import sys, json; json.load(sys.stdin)' 2>/dev/null; then
    ok "--json output parses as valid JSON"
else
    ng "--json output parses as valid JSON" "json.load() raised"
fi
contains "$out" "\"schema_version\": 1" "--json has schema_version 1"
contains "$out" "\"generated_at\":"     "--json has generated_at"
contains "$out" "\"scope\": \"all_time\"" "--json scope reflects --all"
contains "$out" "\"summary\":"          "--json has summary block"
contains "$out" "\"drs\":"              "--json has drs block"
contains "$out" "\"projects\":"         "--json has projects block"
not_contains "$out" "DRS Estimate"      "--json suppresses text header"
not_contains "$out" "Per-Project Breakdown" "--json suppresses breakdown header"

# 11. --json + --anonymize swaps real names for project-N in the projects list.
out=$("$PY" "$CLI" --path "$FIXTURES" --all --json --anonymize 2>&1)
contains "$out" "project-1"  "--json --anonymize uses project-1"
not_contains "$out" "projectA" "--json --anonymize scrubs projectA"
not_contains "$out" "projectB" "--json --anonymize scrubs projectB"

# 12. --json + --share is rejected with a clear error (exit 2).
out=$("$PY" "$CLI" --path "$FIXTURES" --json --share 2>&1) && rc=0 || rc=$?
[ "$rc" -eq 2 ] && ok "--json + --share rejected with rc=2" || ng "--json + --share rejected" "rc=$rc"
contains "$out" "mutually exclusive" "--json + --share error explains why"

# 13. --json without --all returns scope=today even when today is empty -- no
#     silent fallback to all_time. Text mode keeps the fallback (UX nicety),
#     but JSON consumers must get a literal answer to what they asked for.
out=$("$PY" "$CLI" --path "$FIXTURES" --json 2>&1)
contains "$out" "\"scope\": \"today\"" "--json (no --all) keeps scope=today"
contains "$out" "\"sessions\": 0"      "--json (no --all) reports empty today"
not_contains "$out" "showing all-time" "--json suppresses fallback hint"

# 14. cost_usd is rounded to 2 decimals (cents). Float accumulation otherwise
#     yields ugly tails like 0.41000000000000003 in JSON output.
out=$("$PY" "$CLI" --path "$FIXTURES" --all --json 2>&1)
contains "$out"     "\"cost_usd\": 0.41" "summary.cost_usd rounded to 0.41"
not_contains "$out" "0.4100000"          "summary.cost_usd has no float-noise tail"
contains "$out"     "\"cost_usd\": 0.29" "project cost_usd rounded to cents"

# ----------------------------------------------------------------------------
# summary
# ----------------------------------------------------------------------------

printf "\nresult: %d passed, %d failed\n" "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1

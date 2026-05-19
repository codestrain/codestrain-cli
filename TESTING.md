# CodeStrain CLI — test plan

`codestrain_cli.py` ships zero-deps (stdlib only) and parses Claude Code JSONL. Before public release we want **automated unit tests** for pure logic + **scripted smoke tests** for the CLI surface + **a manual UX checklist** for visual output.

Tests live in `cli/tests/`. Run them from the repo root:

```bash
cd cli && python -m pytest tests/         # unit + integration
cd cli && ./tests/smoke.sh                # CLI surface
```

---

## 1. Unit tests (`cli/tests/test_unit.py`)

Pure functions, no I/O, no JSONL parsing. Fast (< 1 s total).

| Function | Cases |
|---|---|
| `Colors.enabled()` | tty + isatty=True → True · NO_COLOR set → False · TERM=dumb → False · pipe/non-tty → False |
| `c(color, text)` | colors on → wrapped · colors off → bare text |
| `drs_color(r)` | r=80 → GREEN · r=50 → YELLOW · r=20 → RED · boundaries at 34 & 67 |
| `readiness_label(r)` | each tier returns the correct label |
| `estimate_strain(hrs, debug, late, weekend)` | base 4 h → ~? · +late-night penalty (2×) · +weekend penalty (1.5×) · debug-ratio amplifies · clamped 0-21 |
| `estimate_recovery(strain, since_last)` | strain 0 → 100% · high strain → low recovery · short sleep penalty |
| `format_duration(sec)` | < 60 → `Ns` · < 3600 → `Xm Ys` · ≥ 3600 → `Xh Ym` |
| `format_cost(c)` | `$0.00` for 0 · `$0.12` for 0.123 · `$12.34` for 12.34 |
| `format_tokens(n)` | `1000` → `1.0K` · `1_500_000` → `1.5M` · 0 → `0` |

## 2. Integration tests (`cli/tests/test_jsonl.py`)

Drive `find_jsonl_files`, `parse_jsonl`, `extract_session_stats` with **synthetic fixture JSONL** under `cli/tests/fixtures/`. No dependency on the user's `~/.claude/projects/` (so tests are deterministic on any machine).

Fixtures:

```
cli/tests/fixtures/
├── projects/
│   ├── -Users-test-projectA/
│   │   ├── session-001.jsonl    # 3 turns, 5 min, has tokens
│   │   └── session-002.jsonl    # malformed line in the middle (parser must skip)
│   ├── -Users-test-projectB/
│   │   └── session-003.jsonl    # 8 turns, 1 h, debug-heavy
│   └── empty-project/
│       └── empty.jsonl          # 0 lines
└── single_event.jsonl           # one-line valid event for sanity
```

Test cases:
- `find_jsonl_files(fixtures/projects/)` → discovers all 4 .jsonl files
- `find_jsonl_files(..., project_filter="A")` → returns only projectA's files
- `parse_jsonl(session-002.jsonl)` → skips bad line, returns the rest
- `parse_jsonl(empty.jsonl)` → returns `[]` (no exception)
- `extract_session_stats(parsed)` → turn count + token sums + cost match precomputed expected values
- `extract_session_stats([])` → zero stats, no division-by-zero crash

## 3. CLI surface tests (`cli/tests/smoke.sh`)

Bash script that exercises every flag, asserts exit code 0, and greps stdout for expected strings. No pytest needed — just `bash`.

```bash
#!/usr/bin/env bash
# Each command must exit 0 and produce expected key strings.

cli=cli/codestrain_cli.py
fixtures=cli/tests/fixtures

assert_contains() { grep -qF "$2" <<<"$1" || { echo "FAIL: $3"; exit 1; }; }

# 1. --help exits 0, mentions every flag.
out=$(python3 $cli --help)
assert_contains "$out" "--all"     "--help missing --all"
assert_contains "$out" "--project" "--help missing --project"
assert_contains "$out" "--path"    "--help missing --path"
assert_contains "$out" "--no-color""--help missing --no-color"

# 2. Default run against fixture dir — should print today's section.
out=$(python3 $cli --path $fixtures/projects --no-color)
assert_contains "$out" "CodeStrain" "header missing"
assert_contains "$out" "Today"      "today section missing"

# 3. --all aggregates everything (turn count must reflect projectA + projectB).
out=$(python3 $cli --path $fixtures/projects --all --no-color)
assert_contains "$out" "All-Time"   "--all label missing"

# 4. --project filter.
out=$(python3 $cli --path $fixtures/projects --project A --no-color --all)
assert_contains "$out" "projectA"   "--project filter dropped projectA"
# projectB must NOT show up
grep -qF "projectB" <<<"$out" && { echo "FAIL: project filter leaked"; exit 1; }

# 5. NO_COLOR env should strip ANSI sequences.
out=$(NO_COLOR=1 python3 $cli --path $fixtures/projects)
grep -qE $'\033\[' <<<"$out" && { echo "FAIL: NO_COLOR ignored"; exit 1; }

# 6. Custom non-existent path → graceful exit (currently warns + exits non-zero,
#    or exits 0 with empty sections — pin the expected behavior here).
python3 $cli --path /tmp/does-not-exist --no-color >/dev/null
# accept any exit code 0 or 1; if it crashes with traceback, fail.
[ $? -le 1 ] || { echo "FAIL: missing path crashed"; exit 1; }

echo "smoke.sh OK"
```

## 4. Manual UX checklist

Run these from a terminal **with** colors and **without** (`NO_COLOR=1`). What you check:

| # | Step | Pass criteria |
|---|------|---------------|
| 1 | `codestrain` (no flags) | ASCII logo prints. "Today" section shows correct sessions/duration/turns. DRS line colored green/yellow/red matching recovery. |
| 2 | `codestrain --all` | "All-Time" label visible. Numbers > today's by a reasonable factor. |
| 3 | `codestrain --project codestrain` | Output limited to that project's sessions only. No spillover. |
| 4 | `codestrain --no-color` | Zero ANSI escapes (verify with `\| cat`). Layout still readable. |
| 5 | `codestrain \| cat` | Same as #4 — auto-detected non-tty. |
| 6 | `codestrain --help` | All 4 flags shown. Examples block shown. Exit code 0. |
| 7 | `codestrain --path /tmp/empty-dir` (`mkdir /tmp/empty-dir`) | "0 sessions" gracefully. No crash. |
| 8 | Terminal width ≤ 80 cols | Header doesn't wrap weirdly. Divider lines aligned. |
| 9 | Dark terminal (Solarized Dark) | Yellow + Red still readable on dark BG. |
| 10 | Light terminal (default macOS Terminal.app) | Same — pick AMBER over pure yellow if needed. |
| 11 | `time codestrain --all` on 1000+ sessions | Completes in < 2 s on M1. CPU < 200%. |
| 12 | `codestrain` with cycled JSONL (live Claude Code session running) | No file-lock errors; latest turn shows up after re-run. |
| 13 | `codestrain --help \| less` | Pager-safe; no broken escapes. |

## 5. Regression assets to bundle in the public repo

For the public `codestrain-cli` repo, include `tests/fixtures/` so contributors can run the suite without needing their own `~/.claude/projects/`. Keep fixtures **small + synthetic** (no real prompts, no PII). Goal: < 50 KB total.

## 6. CI matrix

When the public repo is up, run on every PR:

| Job | Python | OS | What |
|---|---|---|---|
| `lint` | 3.12 | ubuntu-latest | `ruff check`, `ruff format --check` |
| `unit` | 3.9 / 3.10 / 3.11 / 3.12 / 3.13 | ubuntu-latest | `pytest cli/tests/` |
| `smoke` | 3.11 | macos-latest + ubuntu-latest | `cli/tests/smoke.sh` |

GitHub Actions matrix block lives in `.github/workflows/cli.yml`.

---

## TL;DR — what to run before opening the public-repo PR

```bash
# from repo root
python -m pytest cli/tests/ -v && cli/tests/smoke.sh
```

If both green and the manual checklist passed on M-series macOS + an Intel Mac (or Linux VM), ship it.

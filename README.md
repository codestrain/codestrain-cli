<p align="center"><img src="https://raw.githubusercontent.com/codestrain/codestrain-cli/main/.assets/logo.png" alt="CodeStrain" width="200"/></p>

# CodeStrain CLI

*Your AI coding recovery score, from the terminal.*

<p align="center">
  <a href="https://pypi.org/project/codestrain/"><img src="https://img.shields.io/pypi/v/codestrain.svg?v=2" alt="PyPI version"/></a>
  <a href="https://pypi.org/project/codestrain/"><img src="https://img.shields.io/pypi/pyversions/codestrain.svg?v=2" alt="Python versions"/></a>
  <a href="https://github.com/codestrain/codestrain-cli/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/codestrain.svg?v=2" alt="License: MIT"/></a>
  <a href="https://github.com/codestrain/codestrain-cli/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/codestrain/codestrain-cli/ci.yml?branch=main&v=2" alt="CI status"/></a>
</p>

## What is this

CodeStrain parses the Claude Code JSONL session logs already on your disk (`~/.claude/projects/`) and prints cost, token usage, a Developer Recovery Score (DRS) estimate, and a per-project breakdown. Zero dependencies — Python stdlib only. Read-only — your JSONL never leaves the machine.

## Install

```bash
# one-liner (recommended)
curl -fsSL codestrain.dev/install | sh
```

```bash
# pipx
pipx install codestrain
```

```bash
# uv
uv tool install codestrain
```

## Quick start

```bash
# today's stats (default)
codestrain
```

```bash
# all-time, every session ever logged
codestrain --all
```

```bash
# all-time, with project names hashed
codestrain --all --anonymize
```

## Example output

```
   ______          __     _____ __             
  / ____/___  ____/ /__  / ___// /__________ _( )___
 / /   / __ \/ __  / _ \ \__ \/ __/ ___/ __ `/ / __ \
/ /___/ /_/ / /_/ /  __/___/ / /_/ /  / /_/ / / / / /
\____/\____/\__._/\___//____/\__/_/   \__._/_/_/ /_/

  Your AI coding recovery score.

--- All Time ------------------------------------------

  Sessions:  1454
  Duration:  137h 21m  (span 15352h 27m)
  Turns:     61007
  Tokens:    2.0M in / 25.4M out
  Cost:      $21948.61
  Models:    claude-haiku-4-5, claude-opus-4-5, claude-opus-4-7 +5 more

  DRS Estimate  (avg per active day · 52 days · 2.6h/day)
  Strain:    9.0/21
  Recovery:  82%
  Readiness: GREEN — Recovered. Good to go.

--- Per-Project Breakdown -----------------------------

  project-1   31h  2m  13638 turns  $7193.92
  project-2   21h 40m   8684 turns  $3652.80
  project-3   15h 32m   4789 turns  $1212.63
  ...
```

## Flags reference

| Flag | Purpose |
|------|---------|
| `--all` | Aggregate every session ever logged instead of just today. |
| `--project NAME` | Only include sessions whose project basename matches `NAME`. |
| `--path DIR` | Read JSONL from `DIR` instead of `~/.claude/projects/`. |
| `--detect` | Scan common locations and print where Claude Code data lives. |
| `--anonymize` | Hash project names before printing the breakdown. |
| `--no-breakdown` | Suppress the per-project breakdown table. |
| `--no-color` | Disable ANSI colors (also honors `NO_COLOR`). |
| `--logo {auto,big,small,none}` | Control the ASCII logo: `big` always, `small` one-liner, `none` off, `auto` picks based on terminal width. |

## DRS — what it actually measures

**Strain (0-21, per active day).** The CLI sums the gaps between consecutive turns that are ≤ 5 minutes — that's the "active coding" duration. Each hour contributes `2.1` strain points, capped at 21. The 5-minute threshold matches the ccusage / Claude Code Usage Monitor convention and is configurable via `CODESTRAIN_GAP_MIN`. Debug-heavy sessions (high error ratio), late-night work (after 22:00), and weekend coding add small penalties.

**Recovery (0-100%).** Recovery moves inversely to strain and is modulated by hours since the last session (sleep proxy). Eight hours off lifts the baseline; high recent strain pulls it down. The local heuristic doesn't have biometric input — it's purely behavioral.

**Readiness.** A traffic-light derived from recovery: **GREEN** at ≥ 67%, **YELLOW** between 34% and 66%, **RED** below 34%. The thresholds match the macOS app and the WHOOP-inspired DRS spec.

This is a heuristic estimate from JSONL logs, not medical advice. The full CodeStrain app refines DRS with ML models, wearable data (HealthKit / WHOOP / Oura), and per-user calibration.

## Why this is privacy-first

- All parsing runs locally. No data ever leaves your machine.
- No telemetry, no opt-in pings, no usage analytics — not even crash reports.
- Your JSONL files are read-only. They are never uploaded, copied, or modified.
- Respects `NO_COLOR` and `FORCE_COLOR` / `CLICOLOR_FORCE` conventions for piping and CI.

## Related projects

- [ccusage](https://github.com/ryoppippi/ccusage) — the npm reference for parsing Claude Code JSONL. Friend, not foe. We follow its session model so numbers line up.
- [Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) — Python alternative with ML burn-rate prediction and a live dashboard.

## Roadmap (v0.1)

- CreatureView — a tiny macOS menu-bar companion that surfaces DRS without opening a terminal (private beta).
- Souls Studio — paid persona pack and custom-character marketplace (Drill Sergeant, Gentle Princess, Sarcastic AI...).
- Magenta-key sprite pipeline v1.2 — clean alpha extraction for community-created creatures.
- Wearable integration — Apple HealthKit, WHOOP, Oura → unified `HealthSnapshot`.

More at [codestrain.dev](https://codestrain.dev).

## Contributing

PRs welcome. Sign your commits with `git commit -s` (DCO) and run the suite before opening one:

```bash
python -m pytest tests/
tests/smoke.sh
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the contribution workflow and [`TESTING.md`](TESTING.md) for the full test matrix.

## License

CodeStrain CLI is MIT-licensed and free for everyone — individuals, teams, companies, and forks — forever for this and every prior release. The CodeStrain hosted service (DRS predictions, ML models, encrypted sync) is a separate paid product; the CLI works fully offline without it.

If we ever introduce a commercial license for a future major version, we will give at least 90 days' notice, keep individuals and small organizations free, and never apply new terms retroactively. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the maintainer's relicensing posture and the DCO sign-off contributors use.

Copyright (c) 2026 LLP HubLab (codestrain.dev).

---

Star this repo if codestrain told you something you didn't know about your last week of AI coding. → [codestrain.dev](https://codestrain.dev)

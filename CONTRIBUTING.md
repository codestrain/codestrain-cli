# Contributing to CodeStrain CLI

Thanks for stopping by. CodeStrain CLI is small, stdlib-only, and built to stay that way.

## Local setup

```bash
git clone https://github.com/codestrain/codestrain-cli.git
cd codestrain-cli

# Optional but recommended — keeps the test deps isolated.
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e . pytest ruff
```

## Run the tests before sending a PR

```bash
.venv/bin/python -m pytest tests/ -v        # 44 unit + integration tests
tests/smoke.sh                              # 16 CLI surface checks
```

CI runs the same suite on macOS and Linux × Python 3.9 / 3.10 / 3.11 / 3.12 / 3.13. If your PR doesn't pass locally, it won't pass there.

For the full test plan (manual UX checklist, fixture format, CI matrix), see [`TESTING.md`](TESTING.md).

## What's in scope

- bug fixes for the existing flag surface (`--all`, `--project`, `--detect`, `--anonymize`, `--no-breakdown`, `--no-color`, `--logo`, `--path`)
- DRS-formula tuning backed by data
- additional Claude Code JSONL schema fields we currently miss
- terminal-compatibility patches (see open issues on Windows / Git Bash)
- documentation, examples, install-script fixes

## What's out of scope

- new runtime dependencies — this project is stdlib-only and stays that way
- emoji in code or docs (project tone is direct)
- features that require the CodeStrain server / ML backend (those live in the closed-source side)

## Sign your commits (DCO)

We use the [Developer Certificate of Origin](https://developercertificate.org/) — no CLA, no paperwork. Just sign off each commit:

```bash
git commit -s -m "fix: handle empty alpha bbox without crashing"
```

The `-s` adds a `Signed-off-by:` line and certifies you wrote the change (or have permission to contribute it) under the project's license.

## Licensing posture

CodeStrain CLI is MIT-licensed today and will stay that way for v0.1.x. If at some point we introduce a commercial license for a future major version, we will (a) announce it at least 90 days in advance, (b) keep individuals and small organizations free, and (c) never apply new terms retroactively — every release tagged before the change keeps its MIT license forever.

By signing off your commit you certify the DCO 1.1. The project maintainer reserves the right to release new versions of the project under additional licenses.

## Reporting issues

Open a [GitHub issue](https://github.com/codestrain/codestrain-cli/issues) with:

- what you ran (`codestrain --all` etc.)
- what you expected
- what happened instead
- `codestrain --version` + `python3 --version` + OS

For privacy: never paste raw JSONL or session content — share the redacted output (`codestrain --all --anonymize --no-color`) instead.

## Maintainer

Built and maintained by Ivan Kononov / LLP HubLab — codestrain.dev.

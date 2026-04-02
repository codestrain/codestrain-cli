# CodeStrain CLI

Your AI coding recovery score from the terminal.

## Install

```bash
curl -o codestrain https://raw.githubusercontent.com/codestrain/cli/main/codestrain_cli.py
chmod +x codestrain
./codestrain
```

Or clone and run directly:

```bash
git clone https://github.com/codestrain/codestrain.git
cd codestrain/cli
python3 codestrain_cli.py
```

## Requirements

- Python 3.9+ (stdlib only, no dependencies)
- Claude Code installed with session data in `~/.claude/projects/`

## Usage

```bash
# Show today's stats
python3 codestrain_cli.py

# Show all-time stats
python3 codestrain_cli.py --all

# Filter by project name
python3 codestrain_cli.py --project myapp

# Use a custom JSONL directory
python3 codestrain_cli.py --path ~/my-logs/

# Disable colored output
python3 codestrain_cli.py --no-color
```

## What It Shows

- **Session count** -- number of coding sessions detected
- **Total time** -- aggregated session duration
- **Total cost** -- estimated API cost from token usage
- **DRS estimate** -- Developer Recovery Score (strain, recovery, readiness)
- **Per-project breakdown** -- stats grouped by project

## DRS (Developer Recovery Score)

The DRS is a composite metric inspired by WHOOP:

- **Strain** (0-21): weighted coding hours + debug spirals + late-night/weekend penalties
- **Recovery** (0-100%): inferred from session gaps and break patterns
- **Readiness**: GREEN (67-100%), YELLOW (34-66%), RED (0-33%)

The CLI provides a simplified local estimate. The full CodeStrain app uses ML models for more accurate predictions.

## Privacy

All data is read locally from your machine. Nothing is sent anywhere. The CLI only reads JSONL files that Claude Code already stores on your disk.

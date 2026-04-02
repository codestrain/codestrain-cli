#!/usr/bin/env python3
"""
CodeStrain CLI -- Your AI coding recovery score.
Parses Claude Code JSONL sessions and shows stats.

Usage:
    python codestrain_cli.py              # Show today's stats
    python codestrain_cli.py --all        # Show all-time stats
    python codestrain_cli.py --project X  # Filter by project
    python codestrain_cli.py --help       # Show help
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path


# ── ANSI Colors ──────────────────────────────────────────────────────────────

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    AMBER = "\033[38;5;214m"

    @staticmethod
    def enabled():
        """Respect NO_COLOR and dumb terminal conventions."""
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("TERM") == "dumb":
            return False
        return sys.stdout.isatty()


_colors_on = Colors.enabled()


def c(color, text):
    """Wrap text in ANSI color if output is a terminal."""
    if _colors_on:
        return f"{color}{text}{Colors.RESET}"
    return str(text)


def bold(text):
    return c(Colors.BOLD, text)


# ── DRS Color ────────────────────────────────────────────────────────────────

def drs_color(recovery):
    """Return green/yellow/red color based on recovery percentage."""
    if recovery >= 67:
        return Colors.GREEN
    elif recovery >= 34:
        return Colors.YELLOW
    return Colors.RED


def readiness_label(recovery):
    """Return readiness traffic-light label."""
    if recovery >= 67:
        return c(Colors.GREEN, "GREEN -- Recovered. Good to go.")
    elif recovery >= 34:
        return c(Colors.YELLOW, "YELLOW -- Moderate strain. Take more breaks.")
    return c(Colors.RED, "RED -- High strain. Consider a lighter day.")


# ── JSONL Parsing ────────────────────────────────────────────────────────────

def find_jsonl_files(base_dir, project_filter=None):
    """Walk ~/.claude/projects/ and return list of (project_name, file_path)."""
    base = Path(base_dir)
    if not base.exists():
        return []

    results = []
    for jsonl in base.rglob("*.jsonl"):
        # Derive project name from the directory structure.
        # Typical layout: ~/.claude/projects/<path-hash>/<session>.jsonl
        rel = jsonl.relative_to(base)
        parts = list(rel.parts)
        if len(parts) >= 2:
            project_name = parts[0]
        else:
            project_name = "unknown"

        if project_filter and project_filter.lower() not in project_name.lower():
            continue

        results.append((project_name, jsonl))

    return results


def parse_jsonl(path):
    """Parse a single JSONL file and return a list of event dicts."""
    events = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (OSError, IOError):
        return []
    return events


def extract_session_stats(events):
    """Extract stats from parsed JSONL events."""
    timestamps = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    turn_count = 0
    error_turns = 0
    models_used = set()

    for event in events:
        # Extract timestamp
        ts = event.get("timestamp")
        if ts:
            try:
                if isinstance(ts, str):
                    # ISO format
                    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    timestamps.append(dt)
                elif isinstance(ts, (int, float)):
                    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                    timestamps.append(dt)
            except (ValueError, OSError):
                pass

        # Extract tokens from usage field
        usage = event.get("usage", {})
        if isinstance(usage, dict):
            total_input_tokens += usage.get("input_tokens", 0)
            total_output_tokens += usage.get("output_tokens", 0)

        # Extract cost
        cost = event.get("costUSD", event.get("cost_usd", 0))
        if isinstance(cost, (int, float)):
            total_cost += cost

        # Count turns
        role = event.get("role", event.get("type", ""))
        if role in ("assistant", "user", "tool"):
            turn_count += 1

        # Detect errors
        message = event.get("message", event.get("content", ""))
        if isinstance(message, str):
            lower = message.lower()
            if any(kw in lower for kw in ("error", "exception", "failed", "traceback")):
                error_turns += 1

        # Track models
        model = event.get("model", "")
        if model:
            models_used.add(model)

    # Compute duration
    duration_seconds = 0.0
    start_time = None
    end_time = None
    if timestamps:
        timestamps.sort()
        start_time = timestamps[0]
        end_time = timestamps[-1]
        duration_seconds = (end_time - start_time).total_seconds()

    return {
        "turn_count": turn_count,
        "duration_seconds": duration_seconds,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cost": total_cost,
        "error_turns": error_turns,
        "models": models_used,
        "start_time": start_time,
        "end_time": end_time,
    }


# ── DRS Estimation ───────────────────────────────────────────────────────────

def estimate_strain(total_hours, debug_ratio, is_late_night=False, is_weekend=False):
    """
    Simplified strain estimate (0-21 scale).

    Based on the DRS formula from ARCHITECTURE.md:
    - Base: coding hours * 2.1 (so 10h = max 21)
    - Debug spiral: +3 if error ratio > 30%
    - Late night: +2 if coding after 10pm
    - Weekend: +1.5 if coding on Sat/Sun
    """
    base = min(21.0, total_hours * 2.1)
    debug_penalty = 3.0 if debug_ratio > 0.3 else (1.5 if debug_ratio > 0.15 else 0.0)
    late = 2.0 if is_late_night else 0.0
    weekend = 1.5 if is_weekend else 0.0
    return min(21.0, base + debug_penalty + late + weekend)


def estimate_recovery(strain, hours_since_last):
    """
    Simplified recovery estimate (0-100).

    More hours since last session = more recovery.
    Higher strain = harder to recover.
    """
    # Base recovery from time off (8h sleep = 60% recovery)
    time_recovery = min(80.0, hours_since_last * 7.5)
    # Strain penalty
    strain_penalty = strain * 2.0
    return max(0.0, min(100.0, time_recovery - strain_penalty + 40.0))


# ── Display ──────────────────────────────────────────────────────────────────

def format_duration(seconds):
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"


def format_cost(cost):
    """Format USD cost."""
    if cost < 0.01:
        return "$0.00"
    return f"${cost:.2f}"


def format_tokens(count):
    """Format token count with K/M suffix."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def print_header():
    """Print the CodeStrain CLI header."""
    print()
    print(c(Colors.AMBER, "  ______          __     _____ __             _"))
    print(c(Colors.AMBER, " / ____/___  ____/ /__  / ___// /__________ _(_)___"))
    print(c(Colors.AMBER, "/ /   / __ \\/ __  / _ \\ \\__ \\/ __/ ___/ __ `/ / __ \\"))
    print(c(Colors.AMBER, "/ /___/ /_/ / /_/ /  __/___/ / /_/ /  / /_/ / / / / /"))
    print(c(Colors.AMBER, "\\____/\\____/\\__,_/\\___//____/\\__/_/   \\__,_/_/_/ /_/"))
    print()
    print(c(Colors.DIM, "  Your AI coding recovery score."))
    print()


def print_divider(label=""):
    """Print a section divider."""
    if label:
        print(f"\n{c(Colors.DIM, '---')} {bold(label)} {c(Colors.DIM, '-' * max(1, 50 - len(label)))}")
    else:
        print(c(Colors.DIM, "-" * 56))


def print_session_summary(stats_list, label=""):
    """Print aggregated stats for a list of session stats."""
    if not stats_list:
        print(f"  {c(Colors.DIM, 'No sessions found.')}")
        return

    total_turns = sum(s["turn_count"] for s in stats_list)
    total_duration = sum(s["duration_seconds"] for s in stats_list)
    total_cost = sum(s["total_cost"] for s in stats_list)
    total_input = sum(s["total_input_tokens"] for s in stats_list)
    total_output = sum(s["total_output_tokens"] for s in stats_list)
    total_errors = sum(s["error_turns"] for s in stats_list)
    all_models = set()
    for s in stats_list:
        all_models.update(s["models"])

    # Estimate DRS
    total_hours = total_duration / 3600.0
    debug_ratio = total_errors / max(1, total_turns)

    # Check for late night / weekend
    is_late_night = False
    is_weekend = False
    for s in stats_list:
        if s["end_time"]:
            local_time = s["end_time"].astimezone()
            if local_time.hour >= 22 or local_time.hour < 6:
                is_late_night = True
            if local_time.weekday() >= 5:
                is_weekend = True

    strain = estimate_strain(total_hours, debug_ratio, is_late_night, is_weekend)
    recovery = estimate_recovery(strain, 8.0)  # assume 8h since last session

    drs_col = drs_color(recovery)

    if label:
        print(f"  {bold(label)}")
        print()

    print(f"  Sessions:  {bold(str(len(stats_list)))}")
    print(f"  Duration:  {bold(format_duration(total_duration))}")
    print(f"  Turns:     {bold(str(total_turns))}")
    print(f"  Tokens:    {c(Colors.CYAN, format_tokens(total_input))} in / {c(Colors.CYAN, format_tokens(total_output))} out")
    print(f"  Cost:      {c(Colors.AMBER, format_cost(total_cost))}")

    if all_models:
        models_str = ", ".join(sorted(all_models)[:3])
        if len(all_models) > 3:
            models_str += f" +{len(all_models) - 3} more"
        print(f"  Models:    {c(Colors.DIM, models_str)}")

    print()
    print(f"  {bold('DRS Estimate')}")
    print(f"  Strain:    {c(drs_col, f'{strain:.1f}')}/21")
    print(f"  Recovery:  {c(drs_col, f'{recovery:.0f}%')}")
    print(f"  Readiness: {readiness_label(recovery)}")

    if is_late_night:
        print(f"\n  {c(Colors.YELLOW, 'Late-night coding detected (+2 strain)')}")
    if is_weekend:
        print(f"  {c(Colors.YELLOW, 'Weekend coding detected (+1.5 strain)')}")


def print_project_breakdown(project_stats):
    """Print per-project breakdown."""
    if not project_stats:
        return

    print_divider("Per-Project Breakdown")
    print()

    # Sort by total duration descending
    sorted_projects = sorted(
        project_stats.items(),
        key=lambda x: sum(s["duration_seconds"] for s in x[1]),
        reverse=True,
    )

    for project, stats_list in sorted_projects:
        total_duration = sum(s["duration_seconds"] for s in stats_list)
        total_cost = sum(s["total_cost"] for s in stats_list)
        total_turns = sum(s["turn_count"] for s in stats_list)

        project_display = project[:30] + "..." if len(project) > 30 else project
        print(
            f"  {c(Colors.WHITE, project_display):<36}"
            f"{bold(format_duration(total_duration)):>10}  "
            f"{c(Colors.CYAN, str(total_turns)):>6} turns  "
            f"{c(Colors.AMBER, format_cost(total_cost)):>8}"
        )

    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CodeStrain CLI -- Your AI coding recovery score.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  codestrain                  Show today's stats
  codestrain --all            Show all-time stats
  codestrain --project myapp  Filter by project name
  codestrain --path ~/custom  Use custom JSONL directory
        """,
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show all-time stats instead of just today",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Filter by project name (substring match)",
    )
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Custom path to JSONL directory (default: ~/.claude/projects/)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    args = parser.parse_args()

    if args.no_color:
        global _colors_on
        _colors_on = False

    # Determine base directory
    base_dir = args.path or os.path.expanduser("~/.claude/projects")

    if not os.path.isdir(base_dir):
        print_header()
        print(f"  {c(Colors.RED, 'No Claude Code data found.')}")
        print(f"  Expected JSONL files in: {c(Colors.DIM, base_dir)}")
        print(f"\n  {c(Colors.DIM, 'Start using Claude Code to generate session data.')}")
        print()
        sys.exit(0)

    # Find and parse JSONL files
    files = find_jsonl_files(base_dir, project_filter=args.project)

    if not files:
        print_header()
        if args.project:
            print(f"  {c(Colors.YELLOW, f'No sessions found for project matching: {args.project}')}")
        else:
            print(f"  {c(Colors.DIM, 'No JSONL files found in')} {base_dir}")
        print()
        sys.exit(0)

    # Parse all files and collect stats
    today = datetime.date.today()
    all_stats = []
    project_stats = {}

    for project_name, file_path in files:
        events = parse_jsonl(file_path)
        if not events:
            continue

        stats = extract_session_stats(events)

        # Filter to today if not --all
        if not args.all and stats["start_time"]:
            session_date = stats["start_time"].astimezone().date()
            if session_date != today:
                continue

        if stats["turn_count"] == 0 and stats["duration_seconds"] == 0:
            continue

        all_stats.append(stats)

        if project_name not in project_stats:
            project_stats[project_name] = []
        project_stats[project_name].append(stats)

    # Display results
    print_header()

    time_label = "Today" if not args.all else "All Time"
    if args.project:
        time_label += f" (project: {args.project})"

    print_divider(time_label)
    print()
    print_session_summary(all_stats)

    if len(project_stats) > 1:
        print_project_breakdown(project_stats)

    print()


if __name__ == "__main__":
    main()

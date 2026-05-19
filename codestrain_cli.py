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


# ── Path auto-detect ─────────────────────────────────────────────────────────

# Candidate locations where Claude Code might store JSONL. First hit wins.
DEFAULT_JSONL_CANDIDATES = (
    "~/.claude/projects",
    "~/Library/Application Support/Claude/projects",
    "~/Library/Application Support/ClaudeBar-Probe",
    "~/Library/Application Support/CodexBar-ClaudeProbe",
    "~/.config/claude/projects",       # Linux fallback
    "~/AppData/Roaming/Claude/projects",  # Windows fallback
)


def detect_jsonl_path():
    """Return the first existing default location, or None.

    Used when --path is not given. Walks DEFAULT_JSONL_CANDIDATES and returns
    the first path that has ANY *.jsonl file inside (depth-2 max).
    """
    for cand in DEFAULT_JSONL_CANDIDATES:
        p = Path(os.path.expanduser(cand))
        if not p.exists():
            continue
        # Cheap probe: any *.jsonl two levels down?
        for jsonl in p.rglob("*.jsonl"):
            return p
    return None


def suggest_jsonl_paths():
    """Return a list of (path, jsonl_count) for every candidate that exists."""
    found = []
    for cand in DEFAULT_JSONL_CANDIDATES:
        p = Path(os.path.expanduser(cand))
        if not p.exists():
            continue
        n = sum(1 for _ in p.rglob("*.jsonl"))
        if n > 0:
            found.append((p, n))
    return found


def decode_project_name(encoded):
    """Convert `-Users-konn4-workplace-codestrain` → `codestrain` (basename only).

    Claude Code stores each project's JSONL under a directory whose name is the
    cwd with `/` → `-`. The last segment is the project folder name. Falls back
    to the raw encoded string if it doesn't look like a `-Users-` prefix.
    """
    if not encoded.startswith("-Users-") and not encoded.startswith("-home-"):
        return encoded
    parts = encoded.lstrip("-").split("-")
    return parts[-1] if parts else encoded


# ── JSONL Parsing ────────────────────────────────────────────────────────────

def find_jsonl_files(base_dir, project_filter=None):
    """Walk the JSONL root and return list of (project_name, file_path).

    Project name preference:
      1. The first `cwd` field seen inside the first event of the file
         (decoded to a clean basename, e.g. "codestrain").
      2. Fallback: decoded directory name (-Users-foo-bar-baz → baz).
    """
    base = Path(base_dir)
    if not base.exists():
        return []

    results = []
    for jsonl in base.rglob("*.jsonl"):
        rel = jsonl.relative_to(base)
        parts = list(rel.parts)
        encoded_dir = parts[0] if len(parts) >= 2 else "unknown"

        # Try to read `cwd` from the first parseable event in the file
        project_name = None
        try:
            with jsonl.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd = d.get("cwd")
                    if isinstance(cwd, str) and cwd:
                        project_name = Path(cwd).name or Path(cwd).parent.name
                        break
        except OSError:
            pass

        if not project_name:
            project_name = decode_project_name(encoded_dir)

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
    """Extract stats from parsed JSONL events.

    Token + model + cost are read from `event["message"]["usage"]` / `event["message"]["model"]`
    — that's where Claude Code actually writes them. The older top-level layout is kept as
    a fallback for any other JSONL flavor a user might point us at.

    Cost is COMPUTED from token counts × `MODEL_PRICING_USD_PER_MTOK` (Claude Code does not
    write a costUSD field). Includes cache-creation + cache-read tokens, priced separately.
    """
    timestamps = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_creation_tokens = 0
    total_cache_read_tokens = 0
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
                    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    timestamps.append(dt)
                elif isinstance(ts, (int, float)):
                    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                    timestamps.append(dt)
            except (ValueError, OSError):
                pass

        # Tokens + model live inside event["message"] for Claude Code; fall back to
        # top-level for synthetic fixtures or other tools.
        msg = event.get("message") if isinstance(event.get("message"), dict) else {}
        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else event.get("usage", {})

        # Cost preference: explicit costUSD on the event wins (some forks of
        # ccusage / Claude Code variants write it pre-computed). Otherwise
        # compute from tokens × MODEL_PRICING_USD_PER_MTOK below.
        explicit_cost = event.get("costUSD", event.get("cost_usd"))
        if isinstance(explicit_cost, (int, float)):
            total_cost += explicit_cost

        if isinstance(usage, dict):
            inp = int(usage.get("input_tokens") or 0)
            out = int(usage.get("output_tokens") or 0)
            cache_w = int(usage.get("cache_creation_input_tokens") or 0)
            cache_r = int(usage.get("cache_read_input_tokens") or 0)
            total_input_tokens += inp
            total_output_tokens += out
            total_cache_creation_tokens += cache_w
            total_cache_read_tokens += cache_r

            model = msg.get("model") or event.get("model") or ""
            if model:
                models_used.add(model)
                # Only compute pricing-based cost when no explicit costUSD was
                # provided — avoids double-counting in fixture data.
                if not isinstance(explicit_cost, (int, float)):
                    pricing = price_per_mtok_for_model(model)
                    if pricing:
                        p_in, p_out, p_cw, p_cr = pricing
                        total_cost += (
                            inp     / 1_000_000 * p_in
                            + out   / 1_000_000 * p_out
                            + cache_w / 1_000_000 * p_cw
                            + cache_r / 1_000_000 * p_cr
                        )

        # Count turns
        role = event.get("role", event.get("type", ""))
        if role in ("assistant", "user", "tool"):
            turn_count += 1

        # Detect errors — search BOTH a plain string `message` and a structured one
        message = event.get("message", event.get("content", ""))
        text_blob = ""
        if isinstance(message, str):
            text_blob = message
        elif isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                text_blob = content
            elif isinstance(content, list):
                # Claude Code structured content (text + thinking + tool_use blocks)
                pieces = []
                for block in content:
                    if isinstance(block, dict):
                        for k in ("text", "thinking", "content"):
                            v = block.get(k)
                            if isinstance(v, str):
                                pieces.append(v)
                text_blob = " ".join(pieces)
        if text_blob:
            lower = text_blob.lower()
            if any(kw in lower for kw in ("error", "exception", "failed", "traceback")):
                error_turns += 1

    # Compute "active" duration vs wall-clock "span".
    #
    # `duration_seconds` (default reported as "Duration:") is ACTIVE time —
    # sum of gaps between consecutive turns that are ≤ ACTIVE_GAP_THRESHOLD
    # (5 min). This matches the ccusage / Claude Code Usage Monitor convention
    # and reflects real coding time, not the calendar span of a session that
    # may stay open for days.
    #
    # `span_seconds` is kept for advanced views — end_time − start_time of
    # the whole session, idle minutes included.
    duration_seconds = 0.0
    span_seconds = 0.0
    start_time = None
    end_time = None
    if timestamps:
        timestamps.sort()
        start_time = timestamps[0]
        end_time = timestamps[-1]
        span_seconds = (end_time - start_time).total_seconds()
        # Active-time threshold: gap above this between turns ⇒ user went idle.
        # 5 minutes by default; override via CODESTRAIN_GAP_MIN (minutes).
        gap_threshold = max(1, int(os.environ.get("CODESTRAIN_GAP_MIN") or "5")) * 60
        for prev, curr in zip(timestamps, timestamps[1:]):
            gap = (curr - prev).total_seconds()
            if 0 < gap <= gap_threshold:
                duration_seconds += gap

    return {
        "turn_count": turn_count,
        "duration_seconds": duration_seconds,
        "span_seconds": span_seconds,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_creation_tokens": total_cache_creation_tokens,
        "total_cache_read_tokens": total_cache_read_tokens,
        "total_cost": total_cost,
        "error_turns": error_turns,
        "models": models_used,
        "start_time": start_time,
        "end_time": end_time,
    }




# ── Model pricing (USD per 1M tokens) ────────────────────────────────────────
#
# Tuple order: (input, output, cache_creation_write, cache_read).
# Source: Anthropic pricing page snapshot, May 2026. Keep in sync with
# server/ml/training/pricing or ccusage if pricing drifts.

MODEL_PRICING_USD_PER_MTOK = {
    # Claude 4.x family
    "claude-opus-4-7":        (15.00, 75.00, 18.75, 1.50),
    "claude-opus-4-6":        (15.00, 75.00, 18.75, 1.50),
    "claude-opus-4-5":        (15.00, 75.00, 18.75, 1.50),
    "claude-opus-4":          (15.00, 75.00, 18.75, 1.50),
    "claude-sonnet-4-6":      ( 3.00, 15.00,  3.75, 0.30),
    "claude-sonnet-4-5":      ( 3.00, 15.00,  3.75, 0.30),
    "claude-sonnet-4":        ( 3.00, 15.00,  3.75, 0.30),
    "claude-haiku-4-5":       ( 0.80,  4.00,  1.00, 0.08),
    "claude-haiku-4":         ( 0.80,  4.00,  1.00, 0.08),
    # Claude 3.x legacy
    "claude-3-7-sonnet":      ( 3.00, 15.00,  3.75, 0.30),
    "claude-3-5-sonnet":      ( 3.00, 15.00,  3.75, 0.30),
    "claude-3-5-haiku":       ( 0.80,  4.00,  1.00, 0.08),
    "claude-3-opus":          (15.00, 75.00, 18.75, 1.50),
    "claude-3-sonnet":        ( 3.00, 15.00,  3.75, 0.30),
    "claude-3-haiku":         ( 0.25,  1.25,  0.30, 0.03),
}


def price_per_mtok_for_model(model):
    """Return (in, out, cache_w, cache_r) USD per Mtok for a model id, or None.

    Strips a trailing date suffix (e.g. `claude-opus-4-7-20260101`) and falls
    back to family-only prefix matches so we still get a useful price for the
    next minor revision of a model line before this table is updated.
    """
    if not model:
        return None
    if model in MODEL_PRICING_USD_PER_MTOK:
        return MODEL_PRICING_USD_PER_MTOK[model]
    # Strip date suffix (YYYYMMDD at the end)
    parts = model.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        if parts[0] in MODEL_PRICING_USD_PER_MTOK:
            return MODEL_PRICING_USD_PER_MTOK[parts[0]]
    # Prefix fallback: longest matching key
    for key in sorted(MODEL_PRICING_USD_PER_MTOK, key=len, reverse=True):
        if model.startswith(key):
            return MODEL_PRICING_USD_PER_MTOK[key]
    return None


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
    print(c(Colors.AMBER, "   ______          __     _____ __             "))
    print(c(Colors.AMBER, "  / ____/___  ____/ /__  / ___// /__________ _( )___"))
    print(c(Colors.AMBER, " / /   / __ \\/ __  / _ \\ \\__ \\/ __/ ___/ __ `/ / __ \\"))
    print(c(Colors.AMBER, "/ /___/ /_/ / /_/ /  __/___/ / /_/ /  / /_/ / / / / /"))
    print(c(Colors.AMBER, "\\____/\\____/\\__._/\\___//____/\\__/_/   \\__._/_/_/ /_/"))
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

    total_span = sum(s.get("span_seconds", 0) for s in stats_list)

    print(f"  Sessions:  {bold(str(len(stats_list)))}")
    # Duration = active coding time (sum of inter-turn gaps ≤ 5 min).
    # Span    = calendar wall-clock from first to last turn — usually MUCH larger
    #           because Claude Code sessions can stay open across days. We show
    #           both so the user can tell active work apart from idle drift.
    print(f"  Duration:  {bold(format_duration(total_duration))}  "
          f"{c(Colors.DIM, f'(span {format_duration(total_span)})')}")
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


def print_project_breakdown(project_stats, anonymize=False):
    """Print per-project breakdown.

    `anonymize` replaces real project names with `project-1` / `project-2`...
    (preserving the duration-sorted order) so the breakdown can be safely
    shared in screenshots / social media without leaking client names.
    """
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

    for i, (project, stats_list) in enumerate(sorted_projects, start=1):
        total_duration = sum(s["duration_seconds"] for s in stats_list)
        total_cost = sum(s["total_cost"] for s in stats_list)
        total_turns = sum(s["turn_count"] for s in stats_list)

        if anonymize:
            project_display = f"project-{i}"
        else:
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
        help="Custom path to JSONL directory (auto-detected if omitted)",
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="List all detected JSONL locations and exit (no stats shown)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    parser.add_argument(
        "--anonymize",
        action="store_true",
        help="Replace real project names with project-1/project-2/... "
             "(safe for screenshots & social posts)",
    )
    parser.add_argument(
        "--no-breakdown",
        action="store_true",
        help="Skip the per-project breakdown section entirely",
    )

    args = parser.parse_args()

    if args.no_color:
        global _colors_on
        _colors_on = False

    # --detect: scan + report candidates + exit.
    if args.detect:
        print_header()
        found = suggest_jsonl_paths()
        if not found:
            print(f"  {c(Colors.RED, 'No Claude Code data found in any standard location.')}")
            print("  Searched:")
            for cand in DEFAULT_JSONL_CANDIDATES:
                print(f"    {c(Colors.DIM, os.path.expanduser(cand))}")
            print("\n  Pass --path /your/dir if your JSONL lives elsewhere.")
            print()
            sys.exit(1)
        print(f"  {c(Colors.GREEN, 'Detected JSONL locations:')}\n")
        for p, n in found:
            print(f"    {p}  {c(Colors.DIM, f'({n} files)')}")
        print()
        if len(found) == 1:
            print(f"  {c(Colors.DIM, 'Run codestrain (no flags) to use it.')}")
        else:
            print(f"  {c(Colors.DIM, 'Multiple locations found — pass --path to pick one.')}")
        print()
        sys.exit(0)

    # Determine base directory: --path wins; otherwise auto-detect; otherwise legacy default.
    if args.path:
        base_dir = os.path.expanduser(args.path)
    else:
        detected = detect_jsonl_path()
        base_dir = str(detected) if detected else os.path.expanduser("~/.claude/projects")

    if not os.path.isdir(base_dir):
        print_header()
        print(f"  {c(Colors.RED, 'No Claude Code data found.')}")
        print(f"  Tried: {c(Colors.DIM, base_dir)}")
        print()
        print(f"  Run {c(Colors.CYAN, 'codestrain --detect')} to scan for other locations,")
        print(f"  or {c(Colors.CYAN, 'codestrain --path /your/dir')} to point at a custom one.")
        print()
        sys.exit(1)

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
    if args.project and not args.anonymize:
        time_label += f" (project: {args.project})"
    elif args.project and args.anonymize:
        time_label += " (filtered)"

    print_divider(time_label)
    print()
    print_session_summary(all_stats)

    if len(project_stats) > 1 and not args.no_breakdown:
        print_project_breakdown(project_stats, anonymize=args.anonymize)

    print()


if __name__ == "__main__":
    main()

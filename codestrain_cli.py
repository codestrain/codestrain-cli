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

# Keep in sync with pyproject.toml [project].version at every release.
__version__ = "0.1.8"


# ── ANSI Colors ──────────────────────────────────────────────────────────────

def _enable_windows_vt():
    """Enable ANSI virtual-terminal processing on Windows conhost/cmd.

    On non-Windows platforms this is a no-op and returns True.
    On Windows, calls SetConsoleMode with ENABLE_VIRTUAL_TERMINAL_PROCESSING
    (0x0004) so escape sequences render as colors instead of raw `\\033[...m`
    text. Returns False if the call fails — caller should disable colors.
    Stdlib only (ctypes); no `colorama` dep.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VT = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | ENABLE_VT))
    except Exception:
        return False


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
        """Detect whether ANSI color output should be emitted.

        Precedence (high → low):
          1. NO_COLOR env var      → off  (per no-color.org)
          2. TERM == "dumb"        → off
          3. FORCE_COLOR / CLICOLOR_FORCE env var → on
             (per force-color.org + bixense.com/clicolors, lets users pipe to
              `less -R` or capture colored CI logs despite isatty()==False)
          4. sys.stdout.isatty()   → on if attached to a real terminal

        On Windows, ANSI VT processing is enabled via SetConsoleMode; if that
        call fails, colors are silently disabled regardless of the above.
        """
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("TERM") == "dumb":
            return False
        if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
            return _enable_windows_vt()
        if not sys.stdout.isatty():
            return False
        return _enable_windows_vt()


_colors_on = Colors.enabled()


def c(color, text):
    """Wrap text in ANSI color if output is a terminal."""
    if _colors_on:
        return f"{color}{text}{Colors.RESET}"
    return str(text)


def bold(text):
    return c(Colors.BOLD, text)


def hyperlink(url, text=None):
    """Wrap `url` in an OSC 8 escape so terminals make it clickable.

    Supported by iTerm2, macOS Terminal, WezTerm, Kitty, Alacritty, Windows
    Terminal, VSCode terminal, and most modern emulators. Terminals that
    don't understand OSC 8 silently drop the escape (they don't print it as
    garbage) so this is safe to emit unconditionally when colors are on.
    Falls back to plain text when colors are off (CI logs, pipes, etc.) —
    those contexts shouldn't have terminal-only escapes anyway.
    """
    label = text if text is not None else url
    if not _colors_on:
        return label
    return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"


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
    "~/.claude/projects",                        # Claude Code CLI (macOS / Linux)
    "~/Library/Application Support/Claude/projects",  # Claude Desktop on macOS
    "~/.config/claude/projects",                 # XDG / Linux fallback
    "~/AppData/Roaming/Claude/projects",         # Windows fallback
)
# Note: ClaudeBar / CodexBar do NOT write their own JSONLs to disk; they
# invoke `claude` CLI as a subprocess, which logs into ~/.claude/projects/
# under an encoded path containing "ClaudeBar-Probe" or "CodexBar-ClaudeProbe".
# Those entries get filtered later via PROBE_DIR_MARKERS so they don't
# inflate session counts. No need to list them as data sources here.


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

# Tooling probe directories. Menu-bar utilities like ClaudeBar and CodexBar
# spawn diagnostic Claude sessions from their own Application Support dirs,
# which get logged into ~/.claude/projects/ and show up as "Probe" /
# "ClaudeProbe" pseudo-projects in the breakdown. They aren't real user
# coding work, so we filter them out by default. --include-probes opts in.
PROBE_DIR_MARKERS = (
    "ClaudeBar-Probe",
    "CodexBar-ClaudeProbe",
)


def is_probe_directory(encoded_dir: str) -> bool:
    return any(marker in encoded_dir for marker in PROBE_DIR_MARKERS)


def find_jsonl_files(base_dir, project_filter=None, include_probes=False):
    """Walk the JSONL root and return list of (project_name, file_path).

    Project name preference:
      1. The first `cwd` field seen inside the first event of the file
         (decoded to a clean basename, e.g. "codestrain").
      2. Fallback: decoded directory name (-Users-foo-bar-baz → baz).

    By default, tooling probe directories (ClaudeBar / CodexBar diagnostic
    sessions) are skipped — pass include_probes=True to keep them.
    """
    base = Path(base_dir)
    if not base.exists():
        return []

    results = []
    for jsonl in base.rglob("*.jsonl"):
        rel = jsonl.relative_to(base)
        parts = list(rel.parts)
        encoded_dir = parts[0] if len(parts) >= 2 else "unknown"

        if not include_probes and is_probe_directory(encoded_dir):
            continue

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


# ── Report assembly ──────────────────────────────────────────────────────────
#
# build_report() is the single seam between Transform and Load. Aggregation,
# DRS estimation, and the per-project rollup all happen here, returning a
# plain dict. Renderers (text, future --json, future latest.json cache) read
# from that dict and do nothing else. Keep this function pure: no print, no
# ANSI, no terminal-width logic, no file I/O.

REPORT_SCHEMA_VERSION = 1


def build_report(stats_list, project_stats, scope, *, anonymize=False):
    """Aggregate per-session stats into a structured report dict.

    Inputs:
      stats_list    — list of per-session dicts from extract_session_stats().
      project_stats — {project_name: [session_stats, ...]}, already grouped.
      scope         — "today" or "all_time"; informational, included in output.
      anonymize     — when True, project names become project-1/project-2/...
                      in the projects list (duration-sorted order preserved).

    Output: dict with keys schema_version, scope, summary, drs, projects.
    `summary.sessions == 0` signals "no data"; renderers must handle that.
    """
    if not stats_list:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "scope": scope,
            "summary": {
                "sessions": 0,
                "active_seconds": 0.0,
                "span_seconds": 0.0,
                "turns": 0,
                "tokens": {"in": 0, "out": 0},
                "cost_usd": 0.0,
                "models": [],
            },
            "drs": None,
            "projects": [],
        }

    total_turns = sum(s["turn_count"] for s in stats_list)
    total_duration = sum(s["duration_seconds"] for s in stats_list)
    total_span = sum(s.get("span_seconds", 0) for s in stats_list)
    total_cost = sum(s["total_cost"] for s in stats_list)
    total_input = sum(s["total_input_tokens"] for s in stats_list)
    total_output = sum(s["total_output_tokens"] for s in stats_list)
    total_errors = sum(s["error_turns"] for s in stats_list)
    all_models = set()
    for s in stats_list:
        all_models.update(s["models"])

    # DRS — see print_session_summary history for the per-day rationale:
    # strain formula is per active day, so feeding aggregate hours to --all
    # would always saturate at 21/21. We normalize by distinct calendar days.
    total_hours = total_duration / 3600.0
    debug_ratio = total_errors / max(1, total_turns)

    active_days = {
        s["start_time"].astimezone().date()
        for s in stats_list
        if s.get("start_time") is not None
    }
    num_days = max(1, len(active_days))
    hours_per_day = total_hours / num_days

    # Late-night / weekend flags only fire if at least ~10% of the active
    # days hit that pattern — one weekend session a year ago shouldn't flip
    # the flag forever in --all mode.
    late_night_days = 0
    weekend_days = 0
    for s in stats_list:
        if s.get("end_time"):
            local = s["end_time"].astimezone()
            if local.hour >= 22 or local.hour < 6:
                late_night_days += 1
            if local.weekday() >= 5:
                weekend_days += 1
    flag_threshold = max(1, num_days // 10)
    is_late_night = late_night_days >= flag_threshold
    is_weekend = weekend_days >= flag_threshold

    strain = estimate_strain(hours_per_day, debug_ratio, is_late_night, is_weekend)
    recovery = estimate_recovery(strain, 8.0)  # assume 8h since last session

    # Per-project rollup. Sorted by active duration desc so renderers iterate
    # in display order with no further work.
    projects_sorted = sorted(
        project_stats.items(),
        key=lambda x: sum(s["duration_seconds"] for s in x[1]),
        reverse=True,
    )
    projects = []
    for i, (project, plist) in enumerate(projects_sorted, start=1):
        projects.append({
            "name": f"project-{i}" if anonymize else project,
            "active_seconds": sum(s["duration_seconds"] for s in plist),
            "turns": sum(s["turn_count"] for s in plist),
            "cost_usd": sum(s["total_cost"] for s in plist),
        })

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "scope": scope,
        "summary": {
            "sessions": len(stats_list),
            "active_seconds": total_duration,
            "span_seconds": total_span,
            "turns": total_turns,
            "tokens": {"in": total_input, "out": total_output},
            "cost_usd": total_cost,
            "models": sorted(all_models),
        },
        "drs": {
            "strain": strain,
            "strain_max": 21,
            "recovery_pct": recovery,
            "readiness": "GREEN" if recovery >= 67 else ("YELLOW" if recovery >= 34 else "RED"),
            "active_days": num_days,
            "hours_per_day": hours_per_day,
            "late_night": is_late_night,
            "weekend": is_weekend,
        },
        "projects": projects,
    }


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


def _terminal_cols(default=80):
    """Return terminal width in columns; falls back to `default` if unknown.

    Uses `os.get_terminal_size()` which checks the controlling TTY then
    COLUMNS env var. Returns the default on OSError (no TTY, e.g. piped).
    """
    try:
        return os.get_terminal_size().columns
    except (OSError, ValueError):
        return default


def _print_logo_big():
    """Print the full 5-line ASCII logo (needs ≥ 56 cols to render cleanly)."""
    print(c(Colors.AMBER, "   ______          __     _____ __             "))
    print(c(Colors.AMBER, "  / ____/___  ____/ /__  / ___// /__________ _( )___"))
    print(c(Colors.AMBER, " / /   / __ \\/ __  / _ \\ \\__ \\/ __/ ___/ __ `/ / __ \\"))
    print(c(Colors.AMBER, "/ /___/ /_/ / /_/ /  __/___/ / /_/ /  / /_/ / / / / /"))
    print(c(Colors.AMBER, "\\____/\\____/\\__._/\\___//____/\\__/_/   \\__._/_/_/ /_/"))


def _print_logo_small():
    """Print a one-line compact logo for 40-55 col terminals (tmux splits, etc.)."""
    print(c(Colors.AMBER, "[ codestrain ]"))


def print_header_adaptive(mode="auto"):
    """Print the header with a logo sized for the terminal.

    Modes:
      auto  → terminal width: ≥56 = big, 40-55 = small, <40 = no logo
      big   → force 5-line ASCII logo
      small → force one-line `[ codestrain ]`
      none  → skip the logo block entirely (tagline still prints)
    """
    print()
    if mode == "big":
        _print_logo_big()
    elif mode == "small":
        _print_logo_small()
    elif mode == "none":
        pass
    else:  # auto
        cols = _terminal_cols(default=80)
        if cols >= 56:
            _print_logo_big()
        elif cols >= 40:
            _print_logo_small()
        # else: skip logo entirely on cramped terminals
    if mode != "none":
        print()
    print(c(Colors.DIM, f"  Your AI coding recovery score.  v{__version__}"))
    print()


def print_header():
    """Backwards-compatible wrapper — defaults to auto-detected logo size."""
    print_header_adaptive("auto")


def print_divider(label=""):
    """Print a section divider that fits the terminal width.

    Width = min(56, terminal_cols - 2) so dividers don't wrap on narrow
    terminals (tmux splits, ≤ 56-col windows).
    """
    width = max(10, min(56, _terminal_cols(default=80) - 2))
    if label:
        # Room for "--- LABEL " then fill the rest with dashes.
        fill = max(1, width - 4 - len(label) - 1)
        print(f"\n{c(Colors.DIM, '---')} {bold(label)} {c(Colors.DIM, '-' * fill)}")
    else:
        print(c(Colors.DIM, "-" * width))


def print_session_summary(stats_list, label=""):
    """Print aggregated stats for a list of session stats.

    Thin renderer: aggregation + DRS live in build_report(). This function
    only formats text. Passing scope="" because the divider above already
    labels the section; the report's `scope` field isn't used for text out.
    """
    if not stats_list:
        print(f"  {c(Colors.DIM, 'No sessions found.')}")
        return

    report = build_report(stats_list, project_stats={}, scope="")
    s = report["summary"]
    d = report["drs"]

    if label:
        print(f"  {bold(label)}")
        print()

    sessions = s["sessions"]
    active_s = s["active_seconds"]
    span_s = s["span_seconds"]
    turns = s["turns"]
    tok_in = s["tokens"]["in"]
    tok_out = s["tokens"]["out"]
    cost = s["cost_usd"]

    print(f"  Sessions:  {bold(str(sessions))}")
    # Duration = active coding time (sum of inter-turn gaps ≤ 5 min).
    # Span    = calendar wall-clock from first to last turn — usually MUCH larger
    #           because Claude Code sessions can stay open across days. We show
    #           both so the user can tell active work apart from idle drift.
    span_str = f"(span {format_duration(span_s)})"
    print(f"  Duration:  {bold(format_duration(active_s))}  "
          f"{c(Colors.DIM, span_str)}")
    print(f"  Turns:     {bold(str(turns))}")
    print(f"  Tokens:    {c(Colors.CYAN, format_tokens(tok_in))} in / "
          f"{c(Colors.CYAN, format_tokens(tok_out))} out")
    print(f"  Cost:      {c(Colors.AMBER, format_cost(cost))}")

    # Hide "<synthetic>" from the displayed list: it's not a real model, just
    # Claude Code's marker for locally-fabricated events (cached API errors,
    # interrupted turns, no-response slash commands). Token/turn counts still
    # include them — only the user-facing Models row is filtered. The dict
    # carries the full list so --json / cache consumers see everything.
    display_models = [m for m in s["models"] if m != "<synthetic>"]
    if display_models:
        models_str = ", ".join(display_models[:3])
        if len(display_models) > 3:
            models_str += f" +{len(display_models) - 3} more"
        print(f"  Models:    {c(Colors.DIM, models_str)}")

    print()
    strain = d["strain"]
    recovery = d["recovery_pct"]
    active_days = d["active_days"]
    hours_per_day = d["hours_per_day"]
    drs_col = drs_color(recovery)
    if active_days > 1:
        drs_caption = f"(avg per active day · {active_days} days · {hours_per_day:.1f}h/day)"
        print(f"  {bold('DRS Estimate')}  {c(Colors.DIM, drs_caption)}")
    else:
        print(f"  {bold('DRS Estimate')}")
    print(f"  Strain:    {c(drs_col, f'{strain:.1f}')}/21")
    print(f"  Recovery:  {c(drs_col, f'{recovery:.0f}%')}")
    print(f"  Readiness: {readiness_label(recovery)}")

    if d["late_night"]:
        print(f"\n  {c(Colors.YELLOW, 'Late-night coding detected (+2 strain)')}")
    if d["weekend"]:
        print(f"  {c(Colors.YELLOW, 'Weekend coding detected (+1.5 strain)')}")


def print_project_breakdown(project_stats, anonymize=False):
    """Print per-project breakdown.

    Thin renderer over the projects list from build_report(). Sorting and
    aggregation already done there. `anonymize` is forwarded so the dict
    arrives with project-1/project-2/... names instead of real ones (safe
    for screenshots & social shares).
    """
    if not project_stats:
        return

    report = build_report(
        stats_list=[s for plist in project_stats.values() for s in plist],
        project_stats=project_stats,
        scope="",
        anonymize=anonymize,
    )
    if not report["projects"]:
        return

    print_divider("Per-Project Breakdown")
    print()

    # Pre-compute each row's plain (uncoloured) text so we can size columns
    # off the visible width. Padding inside an f-string field on already-
    # coloured strings doesn't work: ANSI escapes count toward the width
    # spec and silently eat the padding, which leaves the breakdown jagged.
    rows = []
    for p in report["projects"]:
        name = p["name"]
        if not anonymize and len(name) > 30:
            name = name[:30] + "..."
        rows.append((
            name,
            format_duration(p["active_seconds"]),
            str(p["turns"]),
            format_cost(p["cost_usd"]),
        ))

    name_w  = max(len(r[0]) for r in rows)
    dur_w   = max(len(r[1]) for r in rows)
    turns_w = max(len(r[2]) for r in rows)
    cost_w  = max(len(r[3]) for r in rows)

    for name, dur, turns, cost in rows:
        print(
            f"  {c(Colors.WHITE, name.ljust(name_w))}  "
            f"{bold(dur.rjust(dur_w))}  "
            f"{c(Colors.CYAN, turns.rjust(turns_w))} turns  "
            f"{c(Colors.AMBER, cost.rjust(cost_w))}"
        )

    print()


# ── Share encoding ───────────────────────────────────────────────────────────
#
# `codestrain --share` produces a single self-contained URL that anyone can
# open to see the same anonymized report. No server, no upload, no cookies —
# the whole report is gzip+base64-encoded into the URL query string. Decoded
# client-side by codestrain.dev/s/index.html.

SHARE_BASE_URL = "https://codestrain.dev/s/"


def build_share_url(text: str, base: str = SHARE_BASE_URL) -> str:
    """Encode `text` into a self-contained share URL.

    Pipeline: utf-8 → gzip (level 9, deterministic) → base64-urlsafe (strip `=`
    padding). The frontend reverses this with DecompressionStream('gzip').
    Average codestrain --all output (~1.5 KB raw) compresses to ~600 B → ~800 B
    base64 → final URL ~ 850 B. Well within all known browser URL limits
    (Chrome / Safari / Firefox / curl all accept up to ~32 KB).
    """
    import base64
    import gzip
    payload = gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0)
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{base.rstrip('/')}/?d={encoded}"


# ── Spinner ──────────────────────────────────────────────────────────────────
#
# Classic `- \ | /` rotation with a rest-themed phrase. Plays while we parse
# JSONL on big histories (the 1-3 second silent pause is the worst part of the
# CLI). Stdlib only — no `yaspin` / `halo` / `rich` dependency.

SPINNER_FRAMES = "-\\|/"

SPINNER_PHRASES = (
    "parsing logs so you don't have to, slowly",
    "doing absolutely nothing, and crushing it",
    "the AI is doing it, I'm at the beach",
    "rate-limiting myself before the universe does",
    "nap-driven development in progress",
    "set status: emotionally unavailable to my linter",
    "romanticizing this loading bar",
    "pretend I'm an out-of-office autoreply",
    "iced coffee, AC on, deadlines elsewhere",
    "touched grass once, billing for the week",
    "running on decaf and delusion",
)


class Spinner:
    """Context-manager spinner. No-op if stdout isn't a TTY or colors are off.

    Picks one phrase at random per run; on operations >3s, rotates to a fresh
    phrase every ~3s. Hides the cursor while running, restores on exit
    (including KeyboardInterrupt — try/finally semantics via __exit__).
    """

    def __init__(self):
        import random
        self._stop = None
        self._thread = None
        self._active = _colors_on and sys.stdout.isatty()
        self._phrase = random.choice(SPINNER_PHRASES)
        self._random = random

    def __enter__(self):
        if not self._active:
            return self
        import threading
        import time
        self._time = time
        self._stop = threading.Event()
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.flush()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        i = 0
        phrase = self._phrase
        start = self._time.monotonic()
        last_rotate = start
        while not self._stop.is_set():
            frame = SPINNER_FRAMES[i % 4]
            line = f"  {c(Colors.YELLOW, frame)} {c(Colors.DIM, phrase)}"
            sys.stdout.write(f"\r{line}\033[K")
            sys.stdout.flush()
            now = self._time.monotonic()
            if now - last_rotate > 3.0:
                phrase = self._random.choice(
                    [p for p in SPINNER_PHRASES if p != phrase]
                )
                last_rotate = now
            i += 1
            self._stop.wait(0.08)

    def __exit__(self, *exc):
        if not self._active:
            return False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.2)
        sys.stdout.write("\r\033[K\033[?25h")  # clear line, show cursor
        sys.stdout.flush()
        return False


def _copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy. Returns True on success, False if no
    suitable system command is available. Never raises.
    """
    import shutil
    import subprocess
    for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"], ["wl-copy"]):
        if shutil.which(cmd[0]):
            try:
                p = subprocess.run(cmd, input=text.encode("utf-8"), timeout=2)
                if p.returncode == 0:
                    return True
            except (OSError, subprocess.TimeoutExpired):
                continue
    return False


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
    parser.add_argument(
        "--logo",
        choices=("auto", "big", "small", "none"),
        default="auto",
        help="Logo variant: auto (default, adapts to terminal width), "
             "big (5-line ASCII), small (one-line), or none (skip logo)",
    )
    parser.add_argument(
        "--share",
        action="store_true",
        help="Generate a shareable codestrain.dev URL with anonymized stats "
             "(implies --anonymize; nothing is uploaded — all data is encoded "
             "in the URL itself, and ANSI colors are preserved in the web view)",
    )
    parser.add_argument(
        "--include-probes",
        action="store_true",
        help="Include diagnostic sessions from menu-bar tools (ClaudeBar, "
             "CodexBar). Excluded by default since they're not user work.",
    )

    args = parser.parse_args()

    # --share is a shortcut that anonymizes and prints a URL. Colors are kept
    # in the encoded payload — the web viewer at codestrain.dev/s parses ANSI
    # escapes back to CSS so the shared report looks the same as the terminal.
    if args.share:
        args.anonymize = True

    if args.no_color:
        global _colors_on
        _colors_on = False

    # --detect: scan + report candidates + exit.
    if args.detect:
        print_header_adaptive(args.logo)
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
        print_header_adaptive(args.logo)
        print(f"  {c(Colors.RED, 'No Claude Code data found.')}")
        print(f"  Tried: {c(Colors.DIM, base_dir)}")
        print()
        print(f"  Run {c(Colors.CYAN, 'codestrain --detect')} to scan for other locations,")
        print(f"  or {c(Colors.CYAN, 'codestrain --path /your/dir')} to point at a custom one.")
        print()
        sys.exit(1)

    # Find and parse JSONL files
    files = find_jsonl_files(
        base_dir,
        project_filter=args.project,
        include_probes=args.include_probes,
    )

    if not files:
        print_header_adaptive(args.logo)
        if args.project:
            print(f"  {c(Colors.YELLOW, f'No sessions found for project matching: {args.project}')}")
        else:
            print(f"  {c(Colors.DIM, 'No JSONL files found in')} {base_dir}")
        print()
        sys.exit(0)

    # Parse all files and collect stats. Spinner during the I/O — JSONL
    # parsing on big histories (1000+ sessions) is the most visible silent
    # pause in the CLI.
    #
    # We parse everything once and then decide between Today and All Time
    # views in-memory. Two reasons:
    #   1. "Today" is filtered by LAST activity (end_time), not first event,
    #      so a session that started yesterday at 23:55 and continued today
    #      still counts as today's work — matches user intuition of
    #      "what did I do today".
    #   2. If --all wasn't passed and today turns out empty, we silently
    #      fall back to the all-time view with a hint, instead of leaving
    #      the user staring at an empty screen.
    today = datetime.date.today()
    scanned = []  # (project_name, stats) for everything that parsed cleanly

    with Spinner():
        for project_name, file_path in files:
            events = parse_jsonl(file_path)
            if not events:
                continue

            stats = extract_session_stats(events)
            if stats["turn_count"] == 0 and stats["duration_seconds"] == 0:
                continue

            scanned.append((project_name, stats))

    def _is_today(stats):
        # Prefer end_time — that's the LAST activity in the session and is
        # what "modified today" means to a human. Fall back to start_time
        # only when no end_time was recoverable from the JSONL.
        ts = stats.get("end_time") or stats.get("start_time")
        return ts is not None and ts.astimezone().date() == today

    fallback_hint = None
    if args.all:
        selected = scanned
    else:
        today_scoped = [(p, s) for p, s in scanned if _is_today(s)]
        if today_scoped:
            selected = today_scoped
        else:
            selected = scanned
            args.all = True  # so the rest of the pipeline labels & filters correctly
            if selected:
                fallback_hint = "No sessions today — showing all-time stats instead."

    all_stats = [s for _, s in selected]
    project_stats = {}
    for project_name, stats in selected:
        project_stats.setdefault(project_name, []).append(stats)

    # Display results — capture stdout into a buffer if --share, so we can
    # encode the rendered report into a URL after the run.
    import contextlib
    import io as _io

    def _render_to(stream):
        with contextlib.redirect_stdout(stream):
            print_header_adaptive(args.logo)
            time_label = "Today" if not args.all else "All Time"
            if args.project and not args.anonymize:
                time_label += f" (project: {args.project})"
            elif args.project and args.anonymize:
                time_label += " (filtered)"
            print_divider(time_label)
            print()
            if fallback_hint:
                print(f"  {c(Colors.DIM, fallback_hint)}")
                print()
            print_session_summary(all_stats)
            if len(project_stats) > 1 and not args.no_breakdown:
                print_project_breakdown(project_stats, anonymize=args.anonymize)
            print()

    if args.share:
        buf = _io.StringIO()
        _render_to(buf)
        report_text = buf.getvalue()
        # Print the report to stdout so the user sees what they're sharing
        sys.stdout.write(report_text)
        share_url = build_share_url(report_text)
        print()
        print(f"  Shareable URL ({len(share_url)} chars):")
        print(f"  {c(Colors.CYAN, hyperlink(share_url))}")
        print()
        # Best-effort clipboard copy on macOS / Linux
        if _copy_to_clipboard(share_url):
            print("  (copied to clipboard)")
            print()
    else:
        _render_to(sys.stdout)


if __name__ == "__main__":
    main()

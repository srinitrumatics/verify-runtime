"""GitHub Actions workflow-command annotations for findings.

Renders findings from a report (as built by ``core.build_json_report``) as
GitHub Actions "workflow command" annotation lines
(``::error``/``::warning``/``::notice``) so CI surfaces them inline on the
PR diff. See:
https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions
"""
from __future__ import annotations

from typing import Any

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

SEVERITY_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "warning",
    "info": "notice",
}


def _esc_data(s: Any) -> str:
    """Escape a workflow-command message/data segment. Order matters: escape
    '%' first so the escape sequences of later replacements aren't themselves
    re-escaped."""
    s = str(s)
    s = s.replace("%", "%25")
    s = s.replace("\r", "%0D")
    s = s.replace("\n", "%0A")
    return s


def _esc_prop(s: Any) -> str:
    """Escape a workflow-command property value (file=, line=, etc). Property
    values need the data escapes plus ':' and ','."""
    s = _esc_data(s)
    s = s.replace(":", "%3A")
    s = s.replace(",", "%2C")
    return s


def _line_sort_key(line: Any):
    # Findings without a usable line sort before/after consistently; the exact
    # order doesn't matter for correctness, only that it's deterministic.
    if isinstance(line, (int, float)):
        return (0, line)
    return (1, 0)


def render_github_annotations(report: dict, cap: int = 50) -> list[str]:
    """Build GitHub Actions annotation lines for every finding across all
    evaluators in `report`. Most-severe findings first; capped at `cap` total
    (with a trailing notice if truncated)."""
    items = []
    for ev in report.get("evaluators") or []:
        stage = ev.get("name", "")
        for f in ev.get("findings") or []:
            severity = f.get("severity", "info")
            rank = SEVERITY_RANK.get(severity, len(SEVERITY_RANK))
            file = f.get("file") or ""
            line = f.get("line")
            message = f.get("message", "")
            items.append((rank, stage, file, line, severity, message))

    items.sort(key=lambda it: (it[0], it[1], it[2], _line_sort_key(it[3])))

    total = len(items)
    truncated = total > cap
    selected = items[:cap]

    lines: list[str] = []
    for rank, stage, file, line, severity, message in selected:
        level = SEVERITY_LEVEL.get(severity, "notice")
        msg = _esc_data(f"[{stage}] {message}")

        props = []
        if file:
            props.append(f"file={_esc_prop(file)}")
            if line:
                props.append(f"line={_esc_prop(line)}")

        if props:
            lines.append(f"::{level} {','.join(props)}::{msg}")
        else:
            lines.append(f"::{level}::{msg}")

    if truncated:
        remaining = total - cap
        lines.append(
            f"::notice::verify: {remaining} more finding(s) not annotated (showing first {cap})"
        )

    return lines


def should_emit(env: dict, flag: bool) -> bool:
    """Whether GitHub Actions annotations should be emitted: either the CLI
    `--github` flag was passed, or we're detectably running inside a GitHub
    Actions job (`GITHUB_ACTIONS=true`)."""
    return bool(flag) or env.get("GITHUB_ACTIONS") == "true"

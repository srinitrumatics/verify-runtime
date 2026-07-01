"""Self-contained static HTML dashboard rendering for a verification report.

``render_html`` takes the same report dict produced by
``verify_runtime.core.build_json_report`` (and optionally a composite-score
trend series from ``verify_runtime.history.trend``) and returns a single
HTML document string: inline CSS only, no external assets/CDNs, readable
with JavaScript disabled.
"""
from __future__ import annotations

import json
from html import escape
from typing import Optional

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

_SEVERITY_COLORS = {
    "critical": "#b91c1c",
    "high": "#c2410c",
    "medium": "#a16207",
    "low": "#1d4ed8",
    "info": "#4b5563",
}


def _e(value: object) -> str:
    """Escape any value for safe inclusion in HTML text."""
    return escape(str(value if value is not None else ""))


def _score_state(score: float, minimum: Optional[float]) -> str:
    if minimum is not None:
        if score >= minimum:
            return "good" if score >= 90 else "ok"
        return "bad"
    if score >= 90:
        return "good"
    if score >= 75:
        return "ok"
    return "bad"


_STATE_COLORS = {"good": "#16a34a", "ok": "#d97706", "bad": "#dc2626", "skip": "#9ca3af"}


def _render_stage_row(ev: dict) -> str:
    name = _e(ev.get("name", ""))
    phase = _e(ev.get("phase", ""))
    skipped = bool(ev.get("skipped"))
    score = float(ev.get("score") or 0.0)
    minimum = ev.get("minimum")

    if skipped or ev.get("passed") is None:
        state = "skip"
        bar_width = 0
        score_label = "SKIP"
    else:
        state = _score_state(score, minimum)
        bar_width = max(0.0, min(100.0, score))
        score_label = f"{score:.1f}"

    color = _STATE_COLORS[state]
    min_label = f'<span class="stage-min">min {_e(minimum)}</span>' if minimum is not None else ""
    summary = _e(ev.get("summary", ""))

    return f"""
    <div class="stage-row">
      <div class="stage-head">
        <span class="stage-name">{name}</span>
        <span class="stage-phase">{phase}</span>
        <span class="stage-score" style="color:{color}">{_e(score_label)}</span>
        {min_label}
      </div>
      <div class="bar-track">
        <div class="bar-fill" style="width:{bar_width:.1f}%;background:{color}"></div>
      </div>
      <div class="stage-summary">{summary}</div>
    </div>"""


def _collect_findings(evaluators: list[dict]) -> list[dict]:
    rows = []
    for ev in evaluators:
        stage_name = ev.get("name", "")
        for f in ev.get("findings") or []:
            rows.append({**f, "_stage": stage_name})
    order = {s: i for i, s in enumerate(SEVERITY_ORDER)}
    rows.sort(key=lambda f: order.get(f.get("severity", "info"), len(order)))
    return rows


def _render_findings_table(evaluators: list[dict], cap: int = 30) -> str:
    findings = _collect_findings(evaluators)
    total = len(findings)
    shown = findings[:cap]
    if not shown:
        return '<p class="empty">No findings recorded.</p>'

    rows_html = []
    for f in shown:
        sev = f.get("severity", "info")
        color = _SEVERITY_COLORS.get(sev, "#4b5563")
        loc = ""
        if f.get("file"):
            loc = _e(f["file"])
            if f.get("line"):
                loc += f":{_e(f['line'])}"
        rows_html.append(f"""
        <tr>
          <td><span class="sev-badge" style="background:{color}">{_e(sev.upper())}</span></td>
          <td>{_e(f.get('_stage', ''))}</td>
          <td>{_e(f.get('message', ''))}</td>
          <td class="loc">{loc}</td>
        </tr>""")

    more_note = ""
    if total > cap:
        more_note = f'<p class="more-note">+{total - cap} more not shown</p>'

    return f"""
    <table class="findings-table">
      <thead>
        <tr><th>Severity</th><th>Stage</th><th>Message</th><th>Location</th></tr>
      </thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
    {more_note}"""


def _render_severity_chips(findings_by_severity: dict) -> str:
    chips = []
    for sev in SEVERITY_ORDER:
        count = findings_by_severity.get(sev, 0)
        color = _SEVERITY_COLORS.get(sev, "#4b5563")
        chips.append(
            f'<span class="chip" style="border-color:{color};color:{color}">'
            f'{_e(sev)}: {_e(count)}</span>'
        )
    return "".join(chips)


def _render_trend_svg(trend: list[float]) -> str:
    if not trend or len(trend) < 2:
        return ""

    width, height, pad = 480, 120, 20
    lo, hi = min(trend), max(trend)
    span = hi - lo if hi != lo else 1.0
    n = len(trend)
    step = (width - 2 * pad) / (n - 1)

    points = []
    for i, v in enumerate(trend):
        x = pad + i * step
        y = height - pad - ((v - lo) / span) * (height - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)

    return f"""
    <div class="trend-section">
      <h2>Trend <span class="trend-range">(min {_e(f'{lo:.1f}')} · max {_e(f'{hi:.1f}')})</span></h2>
      <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" class="trend-svg"
           role="img" aria-label="composite score trend">
        <polyline points="{polyline}" fill="none" stroke="#2563eb" stroke-width="2"/>
      </svg>
    </div>"""


_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: #f8fafc;
  color: #0f172a;
  line-height: 1.5;
}
.wrap { max-width: 900px; margin: 0 auto; padding: 32px 20px 64px; }
header.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 24px;
}
.header-left h1 { margin: 0 0 4px; font-size: 20px; }
.header-left .generated { color: #64748b; font-size: 13px; }
.header-right { display: flex; align-items: center; gap: 16px; }
.composite-score { font-size: 40px; font-weight: 700; }
.gate-badge {
  display: inline-block;
  padding: 6px 14px;
  border-radius: 999px;
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.04em;
}
.gate-pass { background: #dcfce7; color: #166534; }
.gate-fail { background: #fee2e2; color: #991b1b; }
section {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 20px 24px;
  margin-bottom: 20px;
}
section h2 { margin-top: 0; font-size: 16px; }
.stage-row { padding: 10px 0; border-bottom: 1px solid #f1f5f9; }
.stage-row:last-child { border-bottom: none; }
.stage-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.stage-name { font-weight: 600; }
.stage-phase { color: #64748b; font-size: 12px; }
.stage-score { font-weight: 700; margin-left: auto; }
.stage-min { color: #94a3b8; font-size: 12px; }
.bar-track {
  margin-top: 6px;
  height: 8px;
  border-radius: 4px;
  background: #e2e8f0;
  overflow: hidden;
}
.bar-fill { height: 100%; border-radius: 4px; }
.stage-summary { color: #475569; font-size: 13px; margin-top: 4px; }
.findings-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.findings-table th, .findings-table td {
  text-align: left;
  padding: 8px 6px;
  border-bottom: 1px solid #f1f5f9;
  vertical-align: top;
}
.sev-badge {
  color: #fff;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.03em;
}
.loc { color: #64748b; font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.more-note { color: #64748b; font-size: 12px; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chip {
  border: 1px solid;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 600;
}
.trend-range { color: #64748b; font-size: 12px; font-weight: 400; }
.trend-svg { display: block; }
.empty { color: #64748b; font-size: 13px; }
footer.report-footer { text-align: center; color: #94a3b8; font-size: 12px; margin-top: 24px; }
"""


def render_html(report: dict, trend: Optional[list[float]] = None) -> str:
    project = report.get("project", "")
    generated_at = report.get("generated_at", "")
    composite = report.get("composite_score", 0.0)
    gate = report.get("gate") or {}
    gate_passed = bool(gate.get("passed"))
    evaluators = report.get("evaluators") or []
    findings_by_severity = report.get("findings_by_severity") or {}

    gate_class = "gate-pass" if gate_passed else "gate-fail"
    gate_text = "PASS" if gate_passed else "FAIL"

    stages_html = "".join(_render_stage_row(ev) for ev in evaluators)
    findings_html = _render_findings_table(evaluators)
    chips_html = _render_severity_chips(findings_by_severity)
    trend_html = _render_trend_svg(trend or [])

    report_json = json.dumps(report, indent=2).replace("</script>", "<\\/script>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verification Report · {_e(project)}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="wrap">
  <header class="report-header">
    <div class="header-left">
      <h1>{_e(project)}</h1>
      <div class="generated">generated {_e(generated_at)}</div>
    </div>
    <div class="header-right">
      <div class="composite-score">{_e(f'{float(composite):.1f}')}</div>
      <span class="gate-badge {gate_class}">{gate_text}</span>
    </div>
  </header>

  <section class="stages-section">
    <h2>Stages</h2>
    {stages_html if stages_html else '<p class="empty">No evaluators ran.</p>'}
  </section>

  <section class="findings-section">
    <h2>Top findings</h2>
    {findings_html}
  </section>

  <section class="chips-section">
    <h2>Findings by severity</h2>
    <div class="chips">{chips_html}</div>
  </section>

  {'<section class="trend-outer">' + trend_html + '</section>' if trend_html else ''}

  <footer class="report-footer">verify-runtime static HTML report</footer>
</div>
<script type="application/json" id="verify-report">{report_json}</script>
</body>
</html>"""

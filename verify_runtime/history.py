"""Historical run database: persist every verification run to SQLite and
provide query helpers (`recent`, `trend`) for the `verify history` command.

Reuses the JSON report dict produced by ``core.build_json_report`` as the
record payload rather than inventing a new schema.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def db_path(root: Path, rules: dict) -> Path:
    override = ((rules or {}).get("history") or {}).get("path")
    if override:
        return Path(override)
    return Path(root) / ".verify" / "history.db"


def enabled(rules: dict, flag: bool) -> bool:
    if flag:
        return True
    return bool(((rules or {}).get("history") or {}).get("enabled"))


def _git_sha(root: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            sha = out.stdout.strip()
            return sha or None
    except Exception:
        pass
    return None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    git_sha TEXT,
    composite REAL,
    gate_passed INTEGER,
    findings_json TEXT,
    stages_json TEXT,
    report_json TEXT
)
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.commit()


def _stages_from_report(report: dict) -> dict:
    stages: dict = {}
    for ev in report.get("evaluators") or []:
        name = ev.get("name")
        if not name:
            continue
        stages[name] = {"score": ev.get("score"), "skipped": bool(ev.get("skipped"))}
    return stages


def record(root: Path, rules: dict, report: dict) -> Path:
    path = db_path(root, rules)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    try:
        _ensure_schema(conn)
        ts = datetime.now(timezone.utc).isoformat()
        gate = report.get("gate") or {}
        conn.execute(
            "INSERT INTO runs (ts, git_sha, composite, gate_passed, findings_json, "
            "stages_json, report_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                _git_sha(root),
                report.get("composite_score"),
                1 if gate.get("passed") else 0,
                json.dumps(report.get("findings_by_severity") or {}),
                json.dumps(_stages_from_report(report)),
                json.dumps(report),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def recent(root: Path, rules: dict, limit: int = 20) -> list[dict]:
    path = db_path(root, rules)
    if not path.exists():
        return []

    conn = sqlite3.connect(str(path))
    try:
        _ensure_schema(conn)
        cur = conn.execute(
            "SELECT ts, git_sha, composite, gate_passed, findings_json, stages_json "
            "FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for ts, git_sha, composite, gate_passed, findings_json, stages_json in rows:
        out.append({
            "ts": ts,
            "git_sha": git_sha,
            "composite": composite,
            "gate_passed": bool(gate_passed),
            "findings_by_severity": json.loads(findings_json) if findings_json else {},
            "stages": json.loads(stages_json) if stages_json else {},
        })
    return out


def trend(root: Path, rules: dict, stage: Optional[str] = None, limit: int = 20) -> list[float]:
    path = db_path(root, rules)
    if not path.exists():
        return []

    conn = sqlite3.connect(str(path))
    try:
        _ensure_schema(conn)
        cur = conn.execute(
            "SELECT composite, stages_json FROM ("
            "  SELECT id, composite, stages_json FROM runs ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC",
            (limit,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    series: list[float] = []
    for composite, stages_json in rows:
        if stage:
            stages = json.loads(stages_json) if stages_json else {}
            value = (stages.get(stage) or {}).get("score")
        else:
            value = composite
        if value is not None:
            series.append(float(value))
    return series

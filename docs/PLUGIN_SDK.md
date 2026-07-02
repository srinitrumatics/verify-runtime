# Writing a verify-runtime plugin

A plugin adds a **stage** to the verification pipeline. The model is deliberately small:
a plugin is a **plain Python module** that exposes a function `evaluate(ctx)`. There is no
base class to subclass and no registry object to call — if your module has `evaluate`, it is
a valid evaluator. (Sharing one across projects adds a one-line entry-point declaration; see
[Registration](#registration).)

```python
def evaluate(ctx) -> dict:
    return {"passed": True, "score": 100.0, "summary": "ok", "findings": [], "metrics": {}}
```

That is a complete, working plugin.

---

## The `ctx` contract (the only thing a plugin depends on)

`ctx` is a `verify_runtime.Context`. These members are the frozen public surface — a plugin
should use nothing else:

| Member | What it gives you |
| --- | --- |
| `ctx.root` | `pathlib.Path` of the project root |
| `ctx.config` | this stage's YAML block (a dict) — your tunables live here |
| `ctx.rules` | the whole parsed `verification.yaml` (e.g. `severity_weights`) |
| `ctx.targets` / `ctx.target(name)` | present, selected build targets (each has `.path`, `.kind`, `.language`) |
| `ctx.run(cmd, cwd=None, timeout=None)` | run a shell command → `RunResult(code, stdout, stderr, combined, timed_out, ok)` |
| `ctx.which(name)` | absolute path of an executable, or `None` |
| `ctx.iter_source_files(exts, targets=None)` | iterate source files by extension across targets (skips vendor/ignored dirs) |
| `ctx.read(path)` | read a file as text (`""` on error) |
| `ctx.finding(severity, message, **extra)` | build a finding dict (see below) |
| `ctx.score_from_findings(findings, base=100.0)` | 100 − Σ severity penalties, clamped to [0, 100] |
| `ctx.severity_weight(severity)` | the configured penalty for a severity |
| `ctx.log(msg)` | emit a progress line to stderr |
| `ctx.env` | `os.environ` |

You never import the engine for a simple stage. For an AI-assisted stage, the AI client is
available as `from verify_runtime import ai` (`ai.available(ctx)`, `ai.call(ctx, system, user,
schema=..., timeout=...)`).

---

## The result your `evaluate` returns

Return a dict. Every key is optional except that you should return *something* meaningful:

```python
{
    "passed": bool | None,      # None => advisory/unknown (excluded from pass/fail rollup)
    "score": float,             # 0–100; if omitted, derived from passed (100/0)
    "summary": str,             # one line shown in the report
    "findings": list[dict],     # see below
    "metrics": dict,            # freeform structured data, surfaced in --json
}
```

To **skip** a stage (missing toolchain, nothing to check), return
`{"passed": None, "skipped": True, "skip_reason": "...", ...}`. Skipped stages are excluded
from the composite — never scored as 0.

### Findings

Build them with `ctx.finding(severity, message, **extra)`:

```python
ctx.finding("high", "dependency advisory: CVE-2026-…", file="backend", line=None)
```

- `severity` ∈ `critical | high | medium | low | info`. Weights come from
  `severity_weights` in config (defaults: 40/20/8/2/0). `critical` also blocks the gate by
  default (`gate.block_on_severity`).
- `file` / `line` are optional but recommended — they drive editor links and
  GitHub PR annotations.
- Any other keyword becomes a field on the finding (surfaced in `--json`).

### Scoring

The usual pattern: collect findings, then
`score = ctx.score_from_findings(findings)`. That subtracts each finding's severity weight
from 100. Return your own `score` if you compute a domain metric instead (e.g. a coverage %).

---

## Auto-remediation (optional — participates in `--fix`)

Expose `remediate(ctx, findings, ai)` to let your stage self-heal under `verify --fix`:

```python
def remediate(ctx, findings, ai) -> dict:
    # `ai` is None unless the user passed --ai. Use ai.call(...) to generate patches.
    actions = []
    # ...apply safe, deterministic fixes (formatters, etc.)...
    return {"actions": actions, "changed": bool(actions)}
```

Remediation only runs for stages that actually ran (respects `--only`/`--skip`). Deterministic
fixes apply immediately; AI patches are previewed as a diff and only written with `--apply`.

---

## Registration

The runtime resolves a stage's `module:` name in this order — **first match wins**, so a local
file can override anything:

1. **Local** — a `<module>.py` in any directory listed under `plugin_paths` in
   `verification.yaml`. No packaging needed. This is the fast path for project-specific rules.
2. **Entry point** — a `verify.evaluators` entry named `<module>` from an installed package.
   This is how you ship a reusable plugin.
3. **Built-in** — `verify_runtime.evaluators.<module>`.

`verify --list` and `verify plugins` show each stage's resolved source (`local` /
`plugin:<dist>` / `builtin`).

### As a project-local plugin (option 1)

```yaml
# verification.yaml
plugin_paths: [verification/plugins]
verification:
  company_rules:
    module: company_rules      # -> verification/plugins/company_rules.py
    phase: Governance
    weight: 3
```

### As an installable package (option 2)

```toml
# your plugin's pyproject.toml
[project.entry-points."verify.evaluators"]
company_rules = "my_company_verify.company_rules"

# optional: register a self-test suite so `verify selftest` and the `meta` gate run it
[project.entry-points."verify.selftests"]
my_company = "my_company_verify.selftest:run"   # run() returns a unittest.TestSuite
```

---

## Complete worked example

This is the real, shipped example plugin (`ai_dashboard/verification/plugins/company_rules.py`).
It enforces required project files and forbids TODO/FIXME in production source — using only the
`ctx` contract, fully configurable via its stage block.

```python
"""Example project-local plugin — company engineering rules."""
from __future__ import annotations

_REQUIRED_FILES = [("README.md", "medium"), ("LICENSE", "medium"), ("CHANGELOG.md", "low")]
_FORBIDDEN_MARKERS = ("TODO", "FIXME")
_SOURCE_EXTS = (".php", ".ts", ".tsx", ".js", ".jsx")


def _has_file(root, name: str) -> bool:
    if (root / name).exists():
        return True
    stem = name.split(".")[0]
    return any(p.is_file() for p in root.glob(stem + ".*"))


def _is_test_file(name: str) -> bool:
    low = name.lower()
    return "test" in low or low.endswith((".spec.ts", ".spec.tsx", ".spec.js"))


def evaluate(ctx) -> dict:
    cfg = ctx.config or {}
    findings: list[dict] = []

    for name, severity in (cfg.get("required_files") or _REQUIRED_FILES):
        if not _has_file(ctx.root, name):
            findings.append(ctx.finding(severity, f"required project file missing: {name}", file=name))

    if cfg.get("forbid_todos", True):
        markers = tuple(cfg.get("forbidden_markers") or _FORBIDDEN_MARKERS)
        exts = tuple(cfg.get("source_exts") or _SOURCE_EXTS)
        cap = int(cfg.get("max_todo_findings", 10))
        hits = 0
        for path in ctx.iter_source_files(exts):
            if _is_test_file(path.name):
                continue
            for lineno, line in enumerate(ctx.read(path).splitlines(), 1):
                if any(m in line for m in markers):
                    rel = path.relative_to(ctx.root) if path.is_absolute() else path
                    findings.append(ctx.finding("low", f"{markers[0]}/marker left in production source",
                                                file=str(rel), line=lineno))
                    hits += 1
                    break
            if hits >= cap:
                findings.append(ctx.finding("info", f"TODO/FIXME scan capped at {cap} file(s)"))
                break

    score = ctx.score_from_findings(findings)
    passed = not any(f["severity"] in ("critical", "high") for f in findings)
    summary = "company rules: clean" if not findings else f"company rules: {len(findings)} finding(s)"
    return {"passed": passed, "score": score, "summary": summary,
            "findings": findings, "metrics": {"finding_count": len(findings)}}
```

Because it never emits `critical`/`high` and has no `minimum` in its config, it is **advisory**:
it surfaces findings without failing the release gate.

---

## Testing your plugin

- **Unit-test `evaluate` directly** with a stub `ctx` (a small object exposing the members you
  use). Assert on `findings` and `score` for clean vs. defective fixtures — true-positive and
  true-negative.
- **Register a self-test suite** (the `verify.selftests` entry point above) so `verify selftest`
  and the `meta` gate run it alongside the runtime's own suite — *a quality gate you can verify.*
- **Confirm discovery**: `verify plugins` (lists your evaluator + source) and `verify doctor`
  (confirms it resolves and the environment is sane).
- **Run it**: `verify --only <your_stage>` shows just your stage's output.

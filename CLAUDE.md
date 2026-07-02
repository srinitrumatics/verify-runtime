# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`verify-runtime` is a **dependency-free, config-driven verification gate engine**. It discovers
build targets, runs a pipeline of evaluators, aggregates a weight-weighted 0–100 composite score,
and applies a release **gate**. It ships stack-generic evaluators; domain-specific ones come from
plugins. The CLI entry point is `verify` (`verify_runtime.cli:main`).

## Hard constraint: zero runtime dependencies

`dependencies = []` in `pyproject.toml`, and this is load-bearing, not incidental. The package
must run on a bare Python 3.11+ with only the stdlib. Consequences to respect:

- **No PyYAML** — `verify_runtime/yaml.py` is a bundled minimal YAML loader. Config parsing goes
  through `core.load_yaml`; don't reach for a third-party parser.
- **No test framework** — tests are stdlib `unittest`, not pytest.
- Before adding any `import` of a non-stdlib package, stop: it breaks the core promise.

## Common commands

```bash
pip install -e .                                    # editable install (creates the `verify` script)

# Tests (stdlib unittest — no pytest)
python -m unittest discover -s verify_runtime/tests -v
python -m unittest verify_runtime.tests.test_scoring                       # one module
python -m unittest verify_runtime.tests.test_scoring.TestComposite         # one class
python -m unittest verify_runtime.tests.test_scoring.TestComposite.test_weighted_mean  # one test

verify selftest        # runs every registered `verify.selftests` suite (same tests, via entry points)
verify doctor          # health check: versions, config parse, entry points, stage resolution, tools
```

CI (`.github/workflows/ci.yml`) runs on Python 3.11/3.12/3.13 and does exactly two things: the
`unittest discover` above, then `verify selftest`. Keep both green.

## Architecture

The engine is a straight pipeline; each module has one job.

- **`resolver.py`** — resolves a stage's `module:` name to a Python module in a fixed 3-tier order:
  **local file in a `plugin_paths` dir → `verify.evaluators` entry point → built-in
  `verify_runtime.evaluators.<name>`**. This precedence is the whole plugin story; `resolve_source`
  also returns a source label (`local` / `plugin:<dist>` / `builtin`) used by `--list`, `doctor`,
  and `plugins`.

- **`core.py`** — the engine. Contains discovery (`discover_targets`), the evaluator loop
  (`run_evaluators`), scoring (`compute_composite`), the gate (`evaluate_gate`), human/JSON
  rendering, and remediation (`run_remediation`, `_ai_remediate`). Also defines **`Context`** —
  the *only* surface an evaluator depends on (`ctx.run`, `ctx.which`, `ctx.iter_source_files`,
  `ctx.read`, `ctx.finding`, `ctx.score_from_findings`, `ctx.target(s)`, `ctx.config`, `ctx.rules`,
  `ctx.log`). Keep evaluators talking only to `Context`, never to engine internals.

- **`cli.py`** — arg parsing, config discovery (walks up from cwd for `verification.yaml`), and
  subcommand dispatch (`init`, `selftest`, `doctor`, `plugins`, `history`) plus the default
  flag-driven run. Optional outputs (`--json`, `--html`, `--github`, `--history`) all reuse the one
  `build_json_report` dict rather than inventing new schemas.

- **`evaluators/`** — built-in stages: `build`, `tests`, `lint`, `security`, `performance`, `ai`,
  `meta`. Each is a module exposing `evaluate(ctx) -> {passed, score, summary, findings[], metrics{}}`
  (and optionally `remediate(ctx, findings, ai)` for `--fix`).

- **`ai.py`** — thin Anthropic API client used by the `ai` evaluator and `--fix --ai`. No-ops
  gracefully when `ANTHROPIC_API_KEY` is unset; never a hard dependency.

- **`history.py`** (SQLite run log → `.verify/history.db`), **`dashboard.py`** (self-contained HTML
  report), **`annotations.py`** (GitHub Actions annotations) — all consume the JSON report.

### The self-verification loop (important)

The **`meta`** evaluator runs *the verifier's own test suites* via the `verify.selftests` entry
points and turns a failing/empty suite into a **critical** finding that blocks the gate. So the
runtime cannot pass a run without first proving its own tests pass. When you change engine
behavior, the meta stage is what catches a broken invariant — expect `verify selftest` to fail
loudly, and treat that as the signal, not noise.

### Scoring & gate semantics (where subtle bugs live)

- An evaluator is **`counted`** (contributes to the composite) only when `passed is not None`,
  not `skipped`, and `error is None`. Skipped/inconclusive stages are *excluded* from the weighted
  mean, not scored as zero — see `EvalResult.counted` and `compute_composite` (weight-weighted,
  ignores `weight <= 0`).
- `normalise_result` derives a score when a stage omits one: explicit `score` wins; otherwise
  skipped/inconclusive → 0 (but uncounted), else 100 if passed / 0 if failed.
- The gate (`evaluate_gate`) fails on any of: composite `< min_score`, a finding at a
  `block_on_severity` level, a counted stage below its per-stage `minimum`, or a `required_pass`
  stage that failed. Inconclusive required/minimum stages produce **warnings**, not failures.

### Config schema versioning

`stages_config` reads the v2 `verification:` key and falls back to the legacy v1 `evaluators:`
key. Support both when touching stage iteration.

## Conventions

- **Exit codes are contract**: `0` gate pass, `1` gate fail, `2` harness/config error. Preserve them.
- **Windows**: `_force_utf8()` reconfigures stdout/stderr because report glyphs choke cp1252
  consoles. Keep it when adding new entry points.
- Evaluator failures are swallowed into an `error` on the `EvalResult` (the run continues); don't
  let one evaluator crash the whole pipeline.

## Plugin authoring

See `docs/PLUGIN_SDK.md` for the full evaluator contract and a worked example. Register a
shareable evaluator via `[project.entry-points."verify.evaluators"]`, and its self-tests via
`[project.entry-points."verify.selftests"]` so `verify selftest` / the `meta` stage pick them up.

## Releasing

See `PUBLISHING.md` (build, TestPyPI, PyPI, trusted publishing, git-install fallback).

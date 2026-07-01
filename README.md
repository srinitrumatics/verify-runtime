# verify-runtime

A dependency-free, config-driven verification gate engine. It discovers build targets, runs a
pipeline of evaluators, aggregates a weight-weighted 0–100 composite score, and applies a release
**gate** — *evidence before assertions*. Ships the stack-generic evaluators (`build`, `tests`,
`lint`, `security`, `performance`, `ai_review`, `meta`); domain/methodology evaluators come from
plugins.

## Install & use

```bash
pip install verify-runtime
verify init        # scaffold a starter verification.yaml (+ verification/plugins/)
verify             # run the gate (exit 0 pass / 1 gate fail / 2 harness error)
verify --list      # targets + stages
verify selftest    # run every registered self-test suite (this package + installed plugins)
verify --fix       # deterministic remediation; --fix --ai for Claude-generated patches (preview), --apply to write
```

## Configuration (`verification.yaml`)

Stages live under `verification:`; each names an evaluator `module:` and its `weight`/`minimum`.
Plus `targets`, `gate`, `severity_weights`, `plugin_paths`. A minimal YAML loader is bundled, so
PyYAML is not required.

## Writing a plugin evaluator

An evaluator is a module exposing `evaluate(ctx) -> {passed, score, summary, findings[], metrics{}}`
(and optionally `remediate(ctx, findings, ai)` for `--fix`). `ctx` is the only surface it depends
on: `ctx.root`, `ctx.run`, `ctx.which`, `ctx.iter_source_files`, `ctx.read`, `ctx.finding`,
`ctx.score_from_findings`, `ctx.config`, `ctx.rules`, `ctx.log`, `ctx.target(s)`.

See **[docs/PLUGIN_SDK.md](docs/PLUGIN_SDK.md)** for the full authoring guide (contract, result
schema, findings, remediation, registration, and a complete worked example).

Register a shareable evaluator via entry points; the runtime resolves a stage's `module:` in the
order **local `plugin_paths` file → `verify.evaluators` entry point → built-in**:

```toml
[project.entry-points."verify.evaluators"]
myrule = "my_pkg.myrule"
[project.entry-points."verify.selftests"]
mypkg = "my_pkg.selftest:run"     # run() returns a unittest.TestSuite; picked up by `verify selftest` and the meta stage
```

## Self-tested

`verify selftest` runs this package's own stdlib-`unittest` suite (engine, YAML loader, scoring,
gate, resolver, parsers) plus every installed plugin's suite — a quality gate you can verify.

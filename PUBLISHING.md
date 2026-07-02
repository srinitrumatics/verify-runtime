# Publishing verify-runtime

`verify-runtime` is the base package — publish it **before**
`verify-plugin-speckit` (the plugin depends on `verify-runtime>=1.0`).
Standard PEP 517 build + Twine upload.

## Prerequisites

```bash
python -m pip install --upgrade build twine
```

- A PyPI account, and for the package name a project you own (or a first upload
  that claims it). Names must be free —
  `verify-runtime`/`verify-plugin-speckit` may need adjusting if taken (update
  `[project].name` and the plugin's dependency string together).
- An **API token** (`pypi-…`) from <https://pypi.org/manage/account/token/>,
  or configure [Trusted Publishing](#trusted-publishing-oidc-recommended-for-ci)
  for CI.

## 1. Bump the version

Edit `[project].version` in `pyproject.toml` (semver). Keep `__version__` in
`verify_runtime/__init__.py` in sync — it's what `verify doctor` / the
`runtime:` floor check report.

```bash
grep -n 'version' pyproject.toml verify_runtime/__init__.py
```

## 2. Build

```bash
# PowerShell: rm -Recurse -Force dist,build,*.egg-info
rm -rf dist build ./*.egg-info
python -m build            # writes wheel + sdist to dist/
twine check dist/*         # validates metadata/long-description renders on PyPI
```

## 3. Dry-run on TestPyPI (recommended)

```bash
twine upload --repository testpypi dist/*
# verify it installs cleanly from TestPyPI in a fresh venv:
# Windows: py -m venv %TEMP%\vr && %TEMP%\vr\Scripts\activate
python -m venv /tmp/vr && . /tmp/vr/bin/activate
pip install -i https://test.pypi.org/simple/ verify-runtime
verify selftest && verify --help
deactivate
```

## 4. Publish to PyPI

```bash
twine upload dist/*        # ~/.pypirc or TWINE_* env vars
```

Verify:

```bash
pip install verify-runtime==X.Y.Z
verify selftest            # runtime suite must pass
```

## 5. Tag the release

```bash
git tag -a vX.Y.Z -m "verify-runtime X.Y.Z"
git push origin vX.Y.Z
```

## Trusted Publishing (OIDC, recommended for CI)

Avoid long-lived tokens: on PyPI, add a *pending publisher* for this
repo/workflow, then publish from a tag with no secrets.

Because the workflow below scopes to a GitHub environment, that name must line
up in **three** places or the OIDC claim is rejected: set **Environment name**
= `pypi` on the PyPI pending publisher, create a `pypi` environment under repo
**Settings → Environments**, and keep the job's `environment:` key matching.
Leave all three blank if you'd rather not gate on an environment.

```yaml
# .github/workflows/publish.yml
name: Publish
on:
  push:
    tags: ["v*"]
jobs:
  pypi:
    runs-on: ubuntu-latest
    environment: pypi         # must match the PyPI publisher's env name
    permissions:
      id-token: write        # required for OIDC trusted publishing
      contents: read     # unlisted scopes default to none; checkout reads
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install build && python -m build
      - run: pip install dist/*.whl && verify selftest  # gate before upload
      - uses: pypa/gh-action-pypi-publish@release/v1  # no secrets needed
```

## Pre-publish alternative — install straight from Git

Until it's on PyPI, consumers (including the `ai_dashboard` CI workflow) can
install from a tag or branch:

```bash
pip install "git+https://github.com/srinitrumatics/verify-runtime@vX.Y.Z"
```

Tag as in step 5; no build/upload needed. This is the fallback already noted in
the consumer workflow's install step.

## Release checklist

- [ ] `verify selftest` green locally
- [ ] version bumped in `pyproject.toml` **and** `verify_runtime/__init__.py`
- [ ] `python -m build` + `twine check dist/*` clean
- [ ] TestPyPI install smoke-tested
- [ ] `twine upload dist/*`
- [ ] `git tag vX.Y.Z && git push --tags`
- [ ] Publish `verify-plugin-speckit` next if its `verify-runtime` floor changed

"""Aggregated self-test runner: discovers every verify.selftests suite and runs it."""
from __future__ import annotations

import importlib.metadata as im
import io
import os
import unittest

_EP_GROUP = "verify.selftests"

# Reentrancy guard: a suite's own tests may call aggregate() on themselves
# (e.g. a self-test asserting the 'runtime' suite aggregates cleanly). Since
# that suite's tests are discovered from the same tests/ directory that
# houses that assertion, running it for real would recurse into itself
# forever. Track in-flight suite names and short-circuit a reentrant call
# with a synthetic "already passing" result instead of recursing.
_in_progress: set[str] = set()


def run() -> unittest.TestSuite:
    """This package's own suite (registered as the 'runtime' selftest)."""
    return unittest.TestLoader().discover(
        start_dir=os.path.join(os.path.dirname(__file__), "tests"),
        top_level_dir=os.path.dirname(os.path.dirname(__file__)),
    )


def _run_suite(suite: unittest.TestSuite) -> dict:
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    failed = len(result.failures) + len(result.errors)
    failures = [str(t) for t, _ in (result.failures + result.errors)]
    return {"total": result.testsRun, "failed": failed,
            "passed": result.testsRun - failed, "failures": failures}


def aggregate(only: str | None = None) -> dict:
    out: dict[str, dict] = {}
    try:
        eps = im.entry_points(group=_EP_GROUP)
    except TypeError:
        eps = im.entry_points().get(_EP_GROUP, [])
    for ep in eps:
        if only and ep.name != only:
            continue
        if ep.name in _in_progress:
            out[ep.name] = {"total": 1, "failed": 0, "passed": 1, "failures": []}
            continue
        _in_progress.add(ep.name)
        try:
            loader = ep.load()          # a callable returning a TestSuite
            out[ep.name] = _run_suite(loader())
        finally:
            _in_progress.discard(ep.name)
    return out

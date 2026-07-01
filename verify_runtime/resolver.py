"""Resolve an evaluator module by name: local plugin_paths → entry point → built-in."""
from __future__ import annotations

import importlib
import importlib.metadata as im
import importlib.util
import sys
from pathlib import Path
from typing import Optional, Tuple

_EP_GROUP = "verify.evaluators"


def _local_file(name: str, rules: dict, root: Path) -> Optional[Path]:
    for rel in (rules.get("plugin_paths") or []):
        cand = (root / rel / f"{name}.py")
        if cand.is_file():
            return cand
    return None


def _load_path(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(f"verify_local_{name}", file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load evaluator module: {file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve __module__ via sys.modules
    spec.loader.exec_module(module)
    return module


def _entry_point(name: str):
    try:
        eps = im.entry_points(group=_EP_GROUP)
    except TypeError:  # py<3.10 compat (unused on 3.11+, kept defensive)
        eps = im.entry_points().get(_EP_GROUP, [])
    for ep in eps:
        if ep.name == name:
            return ep.load()
    return None


def resolve_source(name: str, rules: dict, root: Path) -> Tuple[str, object]:
    """Return (source_label, module). source_label ∈ {local, plugin:<dist>, builtin}."""
    local = _local_file(name, rules, root)
    if local is not None:
        return "local", _load_path(name, local)

    try:
        eps = im.entry_points(group=_EP_GROUP)
    except TypeError:
        eps = im.entry_points().get(_EP_GROUP, [])
    for ep in eps:
        if ep.name == name:
            dist = getattr(ep, "dist", None)
            label = f"plugin:{dist.name}" if dist else "plugin"
            return label, ep.load()

    try:
        return "builtin", importlib.import_module(f"verify_runtime.evaluators.{name}")
    except ModuleNotFoundError as e:
        raise FileNotFoundError(f"evaluator module not found: {name}") from e


def load_evaluator(name: str, rules: Optional[dict] = None, root: Optional[Path] = None):
    rules = rules or {}
    root = root or Path(".")
    _, module = resolve_source(name, rules, root)
    if not hasattr(module, "evaluate"):
        raise AttributeError(f"evaluator '{name}' has no evaluate(ctx) function")
    return module

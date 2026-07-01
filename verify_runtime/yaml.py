"""Minimal YAML loader (no PyYAML dependency; uses PyYAML if installed)."""
from __future__ import annotations
from pathlib import Path
from typing import Any

import re


# ---------------------------------------------------------------------------
# Minimal YAML loader (used only when PyYAML is unavailable).
# ---------------------------------------------------------------------------
def _strip_comment(line: str) -> str:
    in_s = in_d = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            if idx == 0 or line[idx - 1] in " \t":
                return line[:idx]
    return line


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _split_flow(inner: str) -> list[str]:
    parts, buf, in_s, in_d = [], [], False, False
    for ch in inner:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        if ch == "," and not in_s and not in_d:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _parse_scalar(s: str) -> Any:
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(x.strip()) for x in _split_flow(inner)]
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low in ("null", "~", ""):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


_MAP_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]+\s*:(\s|$)")


def _parse_map(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[dict, int]:
    result: dict = {}
    while i < len(lines):
        cur_indent, content = lines[i]
        if cur_indent != indent or content.startswith("- "):
            break
        key, _, rest = content.partition(":")
        key = _unquote(key.strip())
        rest = rest.strip()
        i += 1
        if rest == "":
            if i < len(lines) and lines[i][0] > indent:
                child, i = _parse_nodes(lines, i, lines[i][0])
                result[key] = child
            else:
                result[key] = None
        else:
            result[key] = _parse_scalar(rest)
    return result, i


def _parse_list(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[list, int]:
    result: list = []
    while i < len(lines):
        cur_indent, content = lines[i]
        if cur_indent != indent or not (content == "-" or content.startswith("- ")):
            break
        item = content[1:].strip()
        i += 1
        if item == "":
            if i < len(lines) and lines[i][0] > indent:
                child, i = _parse_nodes(lines, i, lines[i][0])
                result.append(child)
            else:
                result.append(None)
        elif _MAP_KEY_RE.match(item):
            key_indent = cur_indent + 2
            sub = [(key_indent, item)]
            while i < len(lines) and lines[i][0] >= key_indent and not lines[i][1].startswith("- "):
                sub.append(lines[i])
                i += 1
            m, _ = _parse_map(sub, 0, key_indent)
            result.append(m)
        else:
            result.append(_parse_scalar(item))
    return result, i


def _parse_nodes(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[Any, int]:
    if i >= len(lines):
        return None, i
    _, content = lines[i]
    if content == "-" or content.startswith("- "):
        return _parse_list(lines, i, indent)
    return _parse_map(lines, i, indent)


def _mini_yaml(text: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw in text.replace("\t", "    ").split("\n"):
        stripped = _strip_comment(raw)
        if stripped.strip() == "":
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))
    if not lines:
        return {}
    value, _ = _parse_nodes(lines, 0, lines[0][0])
    return value


def load_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except Exception:
        return _mini_yaml(text)
